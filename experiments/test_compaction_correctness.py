"""EXP-02: Compaction correctness — try to break LWW after compaction.

Tests whether compaction preserves enough clock metadata for correct
conflict resolution when a compacted peer syncs with an uncompacted peer
that has concurrent writes.

The hypothesis: compaction loses per-property clock granularity. A checkpoint
stores one clock per entity (max of all property clocks), but LWW needs
per-property clocks to resolve conflicts on individual properties.

Usage:
    pytest experiments/test_compaction_correctness.py -v
    python experiments/test_compaction_correctness.py
"""

import sys
import pytest
from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import Metric, check_metrics

ONTOLOGY = {
    "node_types": {
        "server": {
            "properties": {
                "status": {"value_type": "string"},
                "name": {"value_type": "string"},
                "port": {"value_type": "int"},
            }
        }
    },
    "edge_types": {
        "CONNECTS": {
            "source_types": ["server"],
            "target_types": ["server"],
        }
    },
}


def _sync(sender, receiver):
    """Bidirectional sync between two stores."""
    offer = receiver.generate_sync_offer()
    payload = sender.receive_sync_offer(offer)
    receiver.merge_sync_payload(payload)

    offer = sender.generate_sync_offer()
    payload = receiver.receive_sync_offer(offer)
    sender.merge_sync_payload(payload)


def _get_props(store, node_id):
    """Get properties dict for a node."""
    node = store.get_node(node_id)
    return node["properties"] if node else None


# ---------------------------------------------------------------------------
# Scenario 1: Per-property clock loss
# ---------------------------------------------------------------------------

def test_compaction_per_property_clock():
    """After compaction, per-property LWW must still resolve correctly.

    The attack: create a node where two properties have DIFFERENT clocks,
    then compact (which stores one clock per entity = max), then merge
    a concurrent update whose clock is BETWEEN the two property clocks.

    Setup:
        1. A creates s1 (status=up@T1, name=alpha@T1)
        2. Sync A→B (B has s1)
        3. B updates status=down@T2 (T2 slightly after T1) — NO sync back
        4. A does dummy writes to advance clock past T2
        5. A updates name=beta@T5 (T5 >> T2)
        6. Now: A has status@T1, name@T5. B has status@T2. T1 < T2 < T5.
        7. A compacts → checkpoint uses max clock T5 for entire entity
        8. After replay: ALL properties get clock T5, including status
        9. Sync compacted A with B

    Without compaction: B's status@T2 > A's status@T1 → status=down (correct)
    With compaction: checkpoint status@T5 > B's status@T2 → status=up (BUG)
    """
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("s1", "server", "S1", {"status": "up", "name": "alpha"})

    # Sync A→B so B has s1
    b = GraphStore("peer-b", ONTOLOGY)
    _sync(a, b)

    # B updates status — B's clock is slightly after A's
    b.update_property("s1", "status", "down")

    # A advances clock FAR past B (dummy writes)
    for i in range(10):
        a.add_node(f"dummy-{i}", "server", f"D{i}", {"status": "up", "name": f"d{i}"})

    # A updates name at a MUCH later clock
    a.update_property("s1", "name", "beta")

    # Verify pre-compaction state via reference sync
    ref = GraphStore("ref", ONTOLOGY)
    _sync(a, ref)
    _sync(b, ref)
    ref_props = _get_props(ref, "s1")
    print(f"  Reference (no compaction): status={ref_props['status']}, name={ref_props['name']}")

    # A compacts
    a.compact()

    # Sync compacted A with B
    snap = a.snapshot()
    a_fresh = GraphStore.from_snapshot("peer-a-fresh", snap)
    _sync(b, a_fresh)

    props = _get_props(a_fresh, "s1")
    assert props is not None, "s1 should exist after sync"

    print(f"  Reference (no compaction): status={ref_props['status']}, name={ref_props['name']}")
    print(f"  Compacted sync: status={props.get('status')}, name={props.get('name')}")

    check_metrics([
        Metric(
            name="compaction_status_matches_reference",
            measured=1 if props["status"] == ref_props["status"] else 0,
            threshold=1,
            op="==",
        ),
        Metric(
            name="compaction_name_matches_reference",
            measured=1 if props["name"] == ref_props["name"] else 0,
            threshold=1,
            op="==",
        ),
    ], label="EXP-02 per-property clock preservation")


# ---------------------------------------------------------------------------
# Scenario 2: Tombstone resurrection (zombie)
# ---------------------------------------------------------------------------

def test_compaction_no_zombie_resurrection():
    """Deleted entities must not reappear after compaction + sync.

    Setup:
        Peer A: add node X, delete node X, compact
        Peer B: had node X from earlier sync, updates X

    If the safety precondition holds (all peers synced before compact),
    B would have the delete. But what if B was offline?
    """
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("s1", "server", "S1", {"status": "up", "name": "ghost"})

    # Sync to B
    b = GraphStore("peer-b", ONTOLOGY)
    _sync(a, b)

    assert b.get_node("s1") is not None, "B should have s1"

    # A deletes s1
    a.remove_node("s1")
    assert a.get_node("s1") is None, "A should not have s1 after delete"

    # A compacts WITHOUT syncing the delete to B (violating safety precondition)
    a.compact()

    # B updates the "dead" node (B doesn't know about the delete)
    b.update_property("s1", "status", "down")

    # Sync B → compacted A
    snap = a.snapshot()
    a_fresh = GraphStore.from_snapshot("peer-a-fresh", snap)
    _sync(b, a_fresh)

    # What should happen? s1 was deleted by A, but B has a concurrent update.
    # With add-wins semantics and proper clocks: it depends on clock ordering.
    # The real question: does the compacted peer handle this gracefully?
    node = a_fresh.get_node("s1")
    print(f"  s1 after zombie test: {node}")
    if node:
        print(f"    -> ZOMBIE: s1 reappeared after compaction")
        print(f"    -> This is expected IF safety precondition was violated")
    else:
        print(f"    -> No zombie: s1 stayed dead")


# ---------------------------------------------------------------------------
# Scenario 3: Edge clock preservation
# ---------------------------------------------------------------------------

def test_compaction_edge_property_clocks():
    """Edge per-property clocks must survive compaction."""
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("s1", "server", "S1", {"status": "up", "name": "s1"})
    a.add_node("s2", "server", "S2", {"status": "up", "name": "s2"})
    a.add_edge("e1", "CONNECTS", "s1", "s2", {"weight": 1})

    # Drive clock forward
    a.add_node("dummy", "server", "D", {"status": "up", "name": "d"})

    # Update edge property at later clock
    a.update_property("e1", "weight", 99)

    # Sync to B
    b = GraphStore("peer-b", ONTOLOGY)
    _sync(a, b)

    # B updates a different edge property at intermediate clock
    b.update_property("e1", "label", "primary")

    # A compacts
    a.compact()

    # Sync
    snap = a.snapshot()
    a_fresh = GraphStore.from_snapshot("peer-a-fresh", snap)
    _sync(b, a_fresh)

    edge = a_fresh.get_edge("e1")
    assert edge is not None, "e1 should exist"
    print(f"  edge props after compacted sync: {edge['properties']}")

    check_metrics([
        Metric(
            name="edge_weight_preserved",
            measured=1 if edge["properties"].get("weight") == 99 else 0,
            threshold=1,
            op="==",
        ),
        Metric(
            name="edge_label_from_concurrent_peer",
            measured=1 if edge["properties"].get("label") == "primary" else 0,
            threshold=1,
            op="==",
        ),
    ], label="EXP-02 edge property clocks")


# ---------------------------------------------------------------------------
# Scenario 4: Multiple compactions
# ---------------------------------------------------------------------------

def test_double_compaction_preserves_state():
    """Compacting twice produces the same result as compacting once."""
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("s1", "server", "S1", {"status": "up", "name": "alpha"})
    a.add_node("s2", "server", "S2", {"status": "down", "name": "beta"})
    a.add_edge("e1", "CONNECTS", "s1", "s2")
    a.update_property("s1", "status", "down")

    a.compact()
    state_after_first = {n["node_id"]: n["properties"] for n in a.all_nodes()}

    a.add_node("s3", "server", "S3", {"status": "up", "name": "gamma"})
    a.compact()
    state_after_second = {n["node_id"]: n["properties"] for n in a.all_nodes()}

    # s1 and s2 from first compaction should be unchanged
    assert state_after_second["s1"] == state_after_first["s1"]
    assert state_after_second["s2"] == state_after_first["s2"]
    assert "s3" in state_after_second


# ---------------------------------------------------------------------------
# Scenario 5: add-wins after compaction
# ---------------------------------------------------------------------------

def test_compaction_add_wins_semantics():
    """Add-wins must work correctly after compaction.

    If peer A: add X, remove X, compact (X excluded)
    And peer B: add X concurrently with higher clock
    After sync: X should exist (add-wins, B's add clock > A's remove clock)
    """
    a = GraphStore("peer-a", ONTOLOGY)
    a.add_node("s1", "server", "S1", {"status": "up", "name": "test"})

    # Sync to B
    b = GraphStore("peer-b", ONTOLOGY)
    _sync(a, b)

    # A removes, B re-adds with updates (driving B's clock higher)
    a.remove_node("s1")

    # B does several operations to advance its clock past A's remove
    for i in range(5):
        b.add_node(f"b-{i}", "server", f"B{i}", {"status": "up", "name": f"b{i}"})
    b.update_property("s1", "status", "revived")

    # A compacts (s1 is tombstoned, excluded from checkpoint)
    a.compact()

    # Sync B → compacted A
    snap = a.snapshot()
    a_fresh = GraphStore.from_snapshot("peer-a-fresh", snap)
    _sync(b, a_fresh)

    node = a_fresh.get_node("s1")
    print(f"  s1 after add-wins test: {node}")

    # B's update to s1 should win (B's clock > A's remove clock)
    # But A's checkpoint has no record of s1 at all — no tombstone, no add clock, nothing.
    # The question: does B's AddNode/UpdateProperty create s1 fresh on a_fresh?
    # Answer: yes, because a_fresh has never seen s1.
    assert node is not None, (
        "s1 should exist — B's concurrent add should win over A's remove"
    )
    assert node["properties"]["status"] == "revived"


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Per-property clock preservation", test_compaction_per_property_clock),
        ("Zombie resurrection", test_compaction_no_zombie_resurrection),
        ("Edge property clocks", test_compaction_edge_property_clocks),
        ("Double compaction", test_double_compaction_preserves_state),
        ("Add-wins after compaction", test_compaction_add_wins_semantics),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n{'='*60}")
        print(f"EXP-02: {name}")
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
    print(f"EXP-02 Summary: {passed} passed, {failed} failed")
    print(f"{'='*60}")
