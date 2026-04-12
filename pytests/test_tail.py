"""C-1.2 + C-1.3: Cursor-based tail subscriptions.

Tests the store.subscribe_from() API:
- Pull-based: consumer calls next_batch() to get entries past cursor.
- Notification: local append and sync merge wake up waiting subscribers.
- Resumable: cursor persists across disconnect/reconnect.
- Independent: multiple subscribers have independent cursors.
"""

import json
import threading
import time

import pytest

from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {"properties": {"name": {"value_type": "string"}}},
    },
    "edge_types": {},
})


def make_store(instance_id: str = "test") -> GraphStore:
    return GraphStore(instance_id, ONTOLOGY)


# -- Basic cursor behavior --


class TestTailBasics:
    def test_subscribe_from_empty_returns_all(self):
        """Empty cursor = full replay (every entry including genesis)."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        store.add_node("n2", "entity", "N2", {"name": "two"})

        sub = store.subscribe_from([])
        entries = sub.next_batch(timeout_ms=100, max_count=100)

        # genesis + n1 + n2 = 3 entries
        assert len(entries) == 3
        sub.close()

    def test_subscribe_from_current_heads_returns_empty(self):
        """Cursor at current heads = nothing new (after timeout)."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})

        sub = store.subscribe_from(store.heads())
        entries = sub.next_batch(timeout_ms=50, max_count=100)

        assert entries == []
        sub.close()

    def test_subscribe_from_partial_cursor_returns_delta(self):
        """Cursor at an older head returns only the delta."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        cursor_at_n1 = store.heads()
        store.add_node("n2", "entity", "N2", {"name": "two"})
        store.add_node("n3", "entity", "N3", {"name": "three"})

        sub = store.subscribe_from(cursor_at_n1)
        entries = sub.next_batch(timeout_ms=100, max_count=100)

        # Should get n2 and n3 (n1 + its ancestors already seen)
        assert len(entries) == 2
        sub.close()

    def test_next_batch_advances_cursor(self):
        """After next_batch, current_cursor reflects the new frontier."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})

        sub = store.subscribe_from([])
        first_cursor = sub.current_cursor()
        assert first_cursor == []

        entries = sub.next_batch(timeout_ms=100, max_count=100)
        assert len(entries) >= 2  # genesis + n1

        new_cursor = sub.current_cursor()
        assert new_cursor == store.heads()
        sub.close()

    def test_next_batch_max_count(self):
        """max_count bounds the batch size."""
        store = make_store()
        for i in range(10):
            store.add_node(f"n{i}", "entity", f"N{i}", {"name": f"node-{i}"})

        sub = store.subscribe_from([])
        batch = sub.next_batch(timeout_ms=100, max_count=3)
        assert len(batch) == 3
        sub.close()

    def test_next_batch_timeout_returns_empty(self):
        """No entries + timeout → empty list (not an error)."""
        store = make_store()
        sub = store.subscribe_from(store.heads())

        t0 = time.perf_counter()
        entries = sub.next_batch(timeout_ms=100, max_count=10)
        elapsed = (time.perf_counter() - t0) * 1000

        assert entries == []
        # Should block approximately the timeout (with slack)
        assert 50 < elapsed < 500
        sub.close()


# -- Notification (the "wake up on new entry" behavior) --


class TestTailNotification:
    def test_waiter_wakes_on_local_append(self):
        """A thread blocked on next_batch wakes when main thread appends."""
        store = make_store()
        sub = store.subscribe_from(store.heads())

        results = []

        def waiter():
            entries = sub.next_batch(timeout_ms=3000, max_count=10)
            results.append(entries)

        t = threading.Thread(target=waiter)
        t.start()

        # Let waiter actually start blocking
        time.sleep(0.05)

        store.add_node("n1", "entity", "N1", {"name": "from-main"})

        t.join(timeout=2)
        assert not t.is_alive(), "waiter did not wake"
        assert len(results) == 1
        assert len(results[0]) == 1
        sub.close()

    def test_multiple_waiters_all_wake(self):
        """Two subscribers both wake on a single append."""
        store = make_store()
        subs = [store.subscribe_from(store.heads()) for _ in range(2)]
        results = [[], []]

        def waiter(idx):
            entries = subs[idx].next_batch(timeout_ms=3000, max_count=10)
            results[idx].extend(entries)

        threads = [threading.Thread(target=waiter, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()

        time.sleep(0.05)
        store.add_node("n1", "entity", "N1", {"name": "one"})

        for t in threads:
            t.join(timeout=2)
            assert not t.is_alive()

        assert len(results[0]) == 1
        assert len(results[1]) == 1
        for s in subs:
            s.close()


# -- Sync integration (notification on merge_sync_payload) --


class TestTailSyncIntegration:
    def test_waiter_wakes_on_sync_merge(self):
        """Entries arriving via sync trigger notification."""
        a = make_store("peer-a")
        b = make_store("peer-b")

        a.add_node("from-b-will-sync", "entity", "remote", {"name": "hi"})
        # Wait, we need b to receive from a. Let's set it up properly:
        # - a has an entry
        # - b subscribes
        # - b receives offer from a
        # - b's subscriber should wake

        sub = b.subscribe_from(b.heads())
        results = []

        def waiter():
            entries = sub.next_batch(timeout_ms=3000, max_count=100)
            results.append(entries)

        t = threading.Thread(target=waiter)
        t.start()

        time.sleep(0.05)

        # Sync from a to b
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        merged = b.merge_sync_payload(payload)
        assert merged > 0

        t.join(timeout=2)
        assert not t.is_alive(), "subscriber did not wake on merge"
        assert len(results) == 1
        # Should have at least the new entry
        assert len(results[0]) >= 1
        sub.close()


# -- Independent cursors --


class TestTailIndependence:
    def test_two_subscribers_independent_cursors(self):
        """Two subscribers at different cursors get different deltas."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        cursor_at_n1 = store.heads()
        store.add_node("n2", "entity", "N2", {"name": "two"})

        sub_old = store.subscribe_from([])        # from beginning
        sub_new = store.subscribe_from(cursor_at_n1)  # from n1

        old_entries = sub_old.next_batch(timeout_ms=100, max_count=100)
        new_entries = sub_new.next_batch(timeout_ms=100, max_count=100)

        # sub_old sees: genesis + n1 + n2 = 3
        # sub_new sees: n2 = 1
        assert len(old_entries) == 3
        assert len(new_entries) == 1

        sub_old.close()
        sub_new.close()


# -- Cursor validity --


class TestTailErrors:
    def test_subscribe_from_unknown_hash_raises(self):
        """Cursor with unknown hash → error (not silent)."""
        store = make_store()
        fake_hash = "00" * 32  # 64 hex chars, valid format but unknown

        # Either subscribe_from raises immediately, or first next_batch raises
        try:
            sub = store.subscribe_from([fake_hash])
            # If it didn't raise, next_batch must raise
            with pytest.raises(Exception):
                sub.next_batch(timeout_ms=100, max_count=10)
            sub.close()
        except Exception:
            pass  # raised at subscribe time, also fine

    def test_subscribe_from_invalid_hex_raises(self):
        """Cursor with non-hex / wrong-length string → error."""
        store = make_store()
        with pytest.raises(Exception):
            store.subscribe_from(["not-a-real-hash"])


# -- C-1.4: Retention + stale cursor after compaction --


class TestTailRetention:
    def test_stale_cursor_raises_after_compaction(self):
        """Compacting past a subscriber's cursor makes next_batch fail."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        old_cursor = store.heads()
        store.add_node("n2", "entity", "N2", {"name": "two"})

        sub = store.subscribe_from(old_cursor)

        # Force compaction — this will replace entries with a checkpoint.
        # old_cursor points to an entry that no longer exists.
        store.compact(safe=False)

        # next_batch must raise (cursor is stale).
        with pytest.raises(Exception):
            sub.next_batch(timeout_ms=100, max_count=10)
        sub.close()

    def test_register_cursor_blocks_compaction(self):
        """A registered active cursor blocks safe compaction."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        old_cursor = store.heads()
        store.add_node("n2", "entity", "N2", {"name": "two"})

        # Register the old (behind) cursor
        store.register_subscriber_cursor(old_cursor)

        # Safe compaction should refuse
        safe, reasons = store.verify_compaction_safe()
        assert not safe
        assert any("cursor" in r.lower() or "subscriber" in r.lower() for r in reasons)

    def test_register_cursor_at_head_allows_compaction(self):
        """A registered cursor AT current heads does not block compaction."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})

        # Register the current heads (caught up)
        store.register_subscriber_cursor(store.heads())

        # No pending delta → compaction OK from the cursor's perspective.
        # (Other checks like peer sync may still block, but not our cursor.)
        # We test that our cursor specifically doesn't contribute a violation.
        safe, reasons = store.verify_compaction_safe()
        # If it's unsafe, it shouldn't be BECAUSE of the cursor.
        if not safe:
            cursor_reasons = [r for r in reasons if "cursor" in r.lower() or "subscriber" in r.lower()]
            assert not cursor_reasons, f"cursor at head should not block: {cursor_reasons}"

    def test_unregister_cursor_unblocks_compaction(self):
        """Unregistering a cursor removes its compaction block."""
        store = make_store()
        store.add_node("n1", "entity", "N1", {"name": "one"})
        old_cursor = store.heads()
        store.add_node("n2", "entity", "N2", {"name": "two"})

        store.register_subscriber_cursor(old_cursor)
        safe1, _ = store.verify_compaction_safe()
        assert not safe1

        store.unregister_subscriber_cursor(old_cursor)
        safe2, reasons = store.verify_compaction_safe()
        # Still may be unsafe for other reasons, but not due to our cursor.
        if not safe2:
            cursor_reasons = [r for r in reasons if "cursor" in r.lower() or "subscriber" in r.lower()]
            assert not cursor_reasons


# -- C-1.6: Notify strategy (coalescing) --


class TestNotifyStrategy:
    def test_set_strategy_string_immediate(self):
        """'immediate' string → sets Immediate strategy."""
        store = make_store()
        store.set_notify_strategy("immediate")
        # No assertion on behavior — just confirm no crash.

    def test_set_strategy_string_coalesced(self):
        """'coalesced' string with min_interval_ms → sets Coalesced."""
        store = make_store()
        store.set_notify_strategy("coalesced", min_interval_ms=5)

    def test_set_strategy_object_immediate(self):
        """ImmediateNotify() object → sets Immediate."""
        from silk import ImmediateNotify
        store = make_store()
        store.set_notify_strategy(ImmediateNotify())

    def test_set_strategy_object_coalesced(self):
        """CoalescedNotify(ms) object → sets Coalesced."""
        from silk import CoalescedNotify
        store = make_store()
        store.set_notify_strategy(CoalescedNotify(min_interval_ms=10))

    def test_set_strategy_unknown_name_raises(self):
        store = make_store()
        with pytest.raises(Exception):
            store.set_notify_strategy("foobar")

    def test_set_strategy_wrong_type_raises(self):
        store = make_store()
        with pytest.raises(Exception):
            store.set_notify_strategy(42)  # not a str, not a strategy object

    def test_immediate_delivers_every_append(self):
        """With Immediate strategy, every append wakes the subscriber."""
        from silk import ImmediateNotify
        store = make_store()
        store.set_notify_strategy(ImmediateNotify())

        sub = store.subscribe_from(store.heads())
        for i in range(5):
            store.add_node(f"n{i}", "entity", f"N{i}", {"name": f"x{i}"})

        # All appends are visible immediately.
        entries = sub.next_batch(timeout_ms=100, max_count=100)
        assert len(entries) == 5
        sub.close()

    def test_coalesced_still_delivers_all_entries(self):
        """Coalescing skips WAKES, not ENTRIES. Subscriber still gets everything."""
        from silk import CoalescedNotify
        store = make_store()
        # Use a 10ms window — all 5 appends in a loop will coalesce into 1 wake.
        store.set_notify_strategy(CoalescedNotify(min_interval_ms=10))

        sub = store.subscribe_from(store.heads())
        for i in range(5):
            store.add_node(f"n{i}", "entity", f"N{i}", {"name": f"x{i}"})

        # Even if notify only fired once, next_batch returns everything past cursor.
        # (timeout needs to be > coalesce window so the wake actually happens)
        entries = sub.next_batch(timeout_ms=200, max_count=100)
        assert len(entries) == 5
        sub.close()

    def test_coalesced_zero_interval_equivalent_to_immediate(self):
        """min_interval_ms=0 behaves like Immediate."""
        from silk import CoalescedNotify
        store = make_store()
        store.set_notify_strategy(CoalescedNotify(min_interval_ms=0))

        sub = store.subscribe_from(store.heads())
        store.add_node("n1", "entity", "N1", {"name": "x"})
        entries = sub.next_batch(timeout_ms=100, max_count=100)
        assert len(entries) == 1
        sub.close()

    def test_coalesced_notify_negative_raises(self):
        """CoalescedNotify rejects negative intervals at construction."""
        from silk import CoalescedNotify
        with pytest.raises(ValueError):
            CoalescedNotify(min_interval_ms=-1)
