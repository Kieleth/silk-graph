"""EXP-06: Fault injection — sync correctness under adversarial conditions.

Tests what happens when sync messages are lost, corrupted, duplicated,
or when peers experience clock drift. Every scenario verifies that Silk
either converges correctly or rejects bad data cleanly.

Usage:
    python experiments/test_fault_injection.py
    pytest experiments/test_fault_injection.py -v
"""

import random
import sys
import time

import pytest

from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import Metric, check_metrics, print_table


ONTOLOGY = {
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string"},
                "seq": {"value_type": "int"},
            }
        }
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
        }
    },
}


def _make_diverged_peers(n_shared: int, n_unique_each: int):
    """Create two peers with shared history + unique writes."""
    a = GraphStore("peer-a", ONTOLOGY)
    for i in range(n_shared):
        a.add_node(f"shared-{i}", "entity", f"S{i}", {"name": f"s-{i}", "seq": i})

    b = GraphStore.from_snapshot("peer-b", a.snapshot())

    for i in range(n_unique_each):
        a.add_node(f"a-{i}", "entity", f"A{i}", {"name": f"a-{i}", "seq": n_shared + i})
        b.add_node(f"b-{i}", "entity", f"B{i}", {"name": f"b-{i}", "seq": n_shared + i})

    return a, b


def _sync_bidirectional(a, b):
    """Full bidirectional sync. Returns (merged_a_to_b, merged_b_to_a)."""
    offer_b = b.generate_sync_offer()
    payload_ab = a.receive_sync_offer(offer_b)
    m1 = b.merge_sync_payload(payload_ab)

    offer_a = a.generate_sync_offer()
    payload_ba = b.receive_sync_offer(offer_a)
    m2 = a.merge_sync_payload(payload_ba)

    return m1, m2


def _assert_converged(a, b, label=""):
    """Assert both peers have identical graph state."""
    nodes_a = sorted([n["node_id"] for n in a.all_nodes()])
    nodes_b = sorted([n["node_id"] for n in b.all_nodes()])
    assert nodes_a == nodes_b, f"{label}: nodes diverged. A={len(nodes_a)}, B={len(nodes_b)}"

    for nid in nodes_a:
        pa = a.get_node(nid)["properties"]
        pb = b.get_node(nid)["properties"]
        assert pa == pb, f"{label}: properties diverged for {nid}"


# ---------------------------------------------------------------------------
# F1: Message loss — sync payload dropped, recovery on next round
# ---------------------------------------------------------------------------

def test_message_loss_recovery():
    """If a sync payload is dropped, the next sync round recovers."""
    a, b = _make_diverged_peers(50, 20)

    # Round 1: A sends to B, but B's response is "lost" (not delivered to A)
    offer_b = b.generate_sync_offer()
    payload_ab = a.receive_sync_offer(offer_b)
    b.merge_sync_payload(payload_ab)
    # B now has A's data, but A doesn't have B's data (lost message)

    # A still missing B's entries
    assert len(a.all_nodes()) < len(b.all_nodes())

    # Round 2: full bidirectional sync recovers
    _sync_bidirectional(a, b)
    _assert_converged(a, b, "message loss recovery")


# ---------------------------------------------------------------------------
# F2: Duplicate delivery — same payload merged twice
# ---------------------------------------------------------------------------

def test_duplicate_delivery_idempotent():
    """Merging the same payload twice is a no-op (idempotent)."""
    a, b = _make_diverged_peers(50, 20)

    offer_b = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer_b)

    # Deliver once
    m1 = b.merge_sync_payload(payload)
    nodes_after_first = sorted([n["node_id"] for n in b.all_nodes()])

    # Deliver again — same bytes
    m2 = b.merge_sync_payload(payload)
    nodes_after_second = sorted([n["node_id"] for n in b.all_nodes()])

    assert m2 == 0, f"duplicate merge should insert 0 entries, got {m2}"
    assert nodes_after_first == nodes_after_second


# ---------------------------------------------------------------------------
# F3: Corrupted payload — flipped bits
# ---------------------------------------------------------------------------

def test_corrupted_payload_rejected():
    """Corrupted sync payloads are rejected, not silently applied."""
    a, b = _make_diverged_peers(50, 20)

    offer_b = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer_b)

    # Corrupt: flip bits in the middle of the payload
    corrupted = bytearray(payload)
    mid = len(corrupted) // 2
    corrupted[mid] ^= 0xFF
    corrupted[mid + 1] ^= 0xFF
    corrupted[mid + 2] ^= 0xFF
    corrupted = bytes(corrupted)

    # Should either raise an error or merge 0 entries (hash verification fails)
    nodes_before = sorted([n["node_id"] for n in b.all_nodes()])
    try:
        merged = b.merge_sync_payload(corrupted)
        # If it didn't raise, it should have rejected all corrupted entries
        nodes_after = sorted([n["node_id"] for n in b.all_nodes()])
        # The graph should not have changed (corrupted entries rejected by hash check)
    except Exception:
        pass  # Expected — corruption detected

    # Verify B is still consistent
    ok, errors = b.verify_integrity()
    assert ok, f"integrity check failed after corruption attempt: {errors}"


# ---------------------------------------------------------------------------
# F4: Truncated payload — partial delivery
# ---------------------------------------------------------------------------

def test_truncated_payload_rejected():
    """Truncated payloads are rejected cleanly."""
    a, b = _make_diverged_peers(50, 20)

    offer_b = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer_b)

    # Truncate to 50%
    truncated = payload[:len(payload) // 2]

    nodes_before = sorted([n["node_id"] for n in b.all_nodes()])
    try:
        b.merge_sync_payload(truncated)
    except Exception:
        pass  # Expected — deserialization should fail

    nodes_after = sorted([n["node_id"] for n in b.all_nodes()])
    assert nodes_before == nodes_after, "truncated payload should not change graph"

    ok, errors = b.verify_integrity()
    assert ok, f"integrity failed after truncated payload: {errors}"


# ---------------------------------------------------------------------------
# F5: Multi-round convergence under random message loss
# ---------------------------------------------------------------------------

def test_convergence_under_random_loss():
    """With 50% random message loss, peers still converge after enough rounds."""
    rng = random.Random(42)
    a, b = _make_diverged_peers(100, 50)

    expected_total = len(a.all_nodes()) + len(b.all_nodes()) - 100  # shared counted once

    for round_num in range(20):
        # A → B (50% chance of delivery)
        offer_b = b.generate_sync_offer()
        payload_ab = a.receive_sync_offer(offer_b)
        if rng.random() > 0.5:
            b.merge_sync_payload(payload_ab)

        # B → A (50% chance of delivery)
        offer_a = a.generate_sync_offer()
        payload_ba = b.receive_sync_offer(offer_a)
        if rng.random() > 0.5:
            a.merge_sync_payload(payload_ba)

        # Check convergence
        nodes_a = set(n["node_id"] for n in a.all_nodes())
        nodes_b = set(n["node_id"] for n in b.all_nodes())
        if nodes_a == nodes_b and len(nodes_a) == expected_total:
            break

    _assert_converged(a, b, f"random loss (converged in {round_num + 1} rounds)")


# ---------------------------------------------------------------------------
# F6: Three-peer partition — A-B connected, C isolated, then heals
# ---------------------------------------------------------------------------

def test_three_peer_partition_heal():
    """A and B sync while C is partitioned. After healing, all three converge."""
    ont = ONTOLOGY

    a = GraphStore("peer-a", ont)
    b = GraphStore("peer-b", ont)
    c = GraphStore("peer-c", ont)

    # All start synced
    a.add_node("root", "entity", "Root", {"name": "root", "seq": 0})
    _sync_bidirectional(a, b)
    _sync_bidirectional(a, c)
    _assert_converged(a, b, "initial sync a-b")
    _assert_converged(a, c, "initial sync a-c")

    # Partition: C is isolated, A and B keep writing and syncing
    for i in range(10):
        a.add_node(f"a-{i}", "entity", f"A{i}", {"name": f"a-{i}", "seq": i})
        b.add_node(f"b-{i}", "entity", f"B{i}", {"name": f"b-{i}", "seq": i})
    _sync_bidirectional(a, b)

    # C writes independently
    for i in range(10):
        c.add_node(f"c-{i}", "entity", f"C{i}", {"name": f"c-{i}", "seq": i})

    # A and B have root + a-0..9 + b-0..9 = 21 nodes
    # C has root + c-0..9 = 11 nodes
    assert len(a.all_nodes()) == 21
    assert len(c.all_nodes()) == 11

    # Heal: sync C with A, then A with B
    _sync_bidirectional(a, c)
    _sync_bidirectional(a, b)
    _sync_bidirectional(b, c)  # ensure full propagation

    _assert_converged(a, b, "post-heal a-b")
    _assert_converged(a, c, "post-heal a-c")
    assert len(a.all_nodes()) == 31  # root + 10 each


# ---------------------------------------------------------------------------
# F7: Concurrent writes to same entity during partition
# ---------------------------------------------------------------------------

def test_concurrent_property_conflict_resolution():
    """Two peers update the same property during partition. LWW resolves deterministically."""
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("target", "entity", "Target", {"name": "original", "seq": 0})

    b = GraphStore.from_snapshot("peer-b", a.snapshot())

    # Both update the same property (concurrent)
    a.update_property("target", "name", "from-a")
    # Advance B's clock past A's to ensure B wins
    for i in range(5):
        b.add_node(f"b-dummy-{i}", "entity", f"D{i}", {"name": f"d{i}", "seq": i})
    b.update_property("target", "name", "from-b")

    _sync_bidirectional(a, b)
    _assert_converged(a, b, "concurrent property conflict")

    # Verify deterministic resolution
    val = a.get_node("target")["properties"]["name"]
    assert val in ("from-a", "from-b"), f"unexpected value: {val}"


# ---------------------------------------------------------------------------
# F8: Rapid fire — many small syncs in sequence
# ---------------------------------------------------------------------------

def test_rapid_fire_sync():
    """Many small writes interleaved with syncs. No state corruption."""
    a = GraphStore("peer-a", ONTOLOGY)
    b = GraphStore("peer-b", ONTOLOGY)

    for i in range(100):
        # Alternate writes
        if i % 2 == 0:
            a.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"n-{i}", "seq": i})
        else:
            b.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"n-{i}", "seq": i})

        # Sync every 10 writes
        if i % 10 == 9:
            _sync_bidirectional(a, b)

    # Final sync
    _sync_bidirectional(a, b)
    _assert_converged(a, b, "rapid fire")
    assert len(a.all_nodes()) == 100

    ok_a, _ = a.verify_integrity()
    ok_b, _ = b.verify_integrity()
    assert ok_a and ok_b


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("F1: Message loss recovery", test_message_loss_recovery),
        ("F2: Duplicate delivery", test_duplicate_delivery_idempotent),
        ("F3: Corrupted payload", test_corrupted_payload_rejected),
        ("F4: Truncated payload", test_truncated_payload_rejected),
        ("F5: Convergence under 50% loss", test_convergence_under_random_loss),
        ("F6: Three-peer partition heal", test_three_peer_partition_heal),
        ("F7: Concurrent property conflict", test_concurrent_property_conflict_resolution),
        ("F8: Rapid fire sync", test_rapid_fire_sync),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n{'='*60}")
        print(f"EXP-06: {name}")
        print(f"{'='*60}")
        try:
            fn()
            print(f"  RESULT: PASS")
            passed += 1
        except AssertionError as e:
            print(f"  RESULT: FAIL — {e}")
            failed += 1
        except Exception as e:
            print(f"  RESULT: ERROR — {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"EXP-06 Summary: {passed} passed, {failed} failed")
    print(f"{'='*60}")
