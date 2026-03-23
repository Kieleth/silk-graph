"""R-02: Sync Quarantine — accept into oplog, hide from graph.

Tests verifying that invalid entries from sync are quarantined (kept in
oplog for CRDT convergence) but invisible in the materialized graph.
Local writes still reject invalid entries immediately.
"""

import json
import pytest
from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {"properties": {}},
        "signal": {"properties": {}}
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {}
        }
    }
})


def _store(instance_id="test"):
    return GraphStore(instance_id, ONTOLOGY)


def _sync_bidirectional(a, b):
    """Full bidirectional sync."""
    for _ in range(2):
        offer = a.generate_sync_offer()
        payload = b.receive_sync_offer(offer)
        a.merge_sync_payload(payload)

        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)


# -- Core quarantine behavior --


def test_invalid_node_type_quarantined_not_visible():
    """R-02: An entry with an invalid node type is quarantined — in oplog but not in graph."""
    # Store A has a different ontology that allows "spaceship"
    extended_ontology = json.dumps({
        "node_types": {
            "entity": {"properties": {}},
            "signal": {"properties": {}},
            "spaceship": {"properties": {}}
        },
        "edge_types": {
            "LINKS": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {}
            }
        }
    })
    store_a = GraphStore("a", extended_ontology)
    store_b = _store("b")

    # A adds a valid "entity" and an "spaceship" (valid for A, invalid for B)
    store_a.add_node("n1", "entity", "Valid node")
    store_a.add_node("n2", "spaceship", "Invalid for B")

    _sync_bidirectional(store_a, store_b)

    # B should have "entity" node but NOT "spaceship" (quarantined)
    assert store_b.get_node("n1") is not None
    assert store_b.get_node("n2") is None  # quarantined

    # B should report quarantined entries
    quarantined = store_b.get_quarantined()
    assert len(quarantined) > 0


def test_quarantined_entries_dont_appear_in_queries():
    """Quarantined entries are invisible to all query methods."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}, "alien": {"properties": {}}},
        "edge_types": {"LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}}
    })
    store_a = GraphStore("a", extended)
    store_b = _store("b")

    store_a.add_node("n1", "entity", "Valid")
    store_a.add_node("n2", "alien", "Quarantined on B")

    _sync_bidirectional(store_a, store_b)

    # Not in any query method
    assert store_b.get_node("n2") is None
    assert "n2" not in [n["node_id"] for n in store_b.all_nodes()]
    assert "n2" not in [n["node_id"] for n in store_b.query_nodes_by_type("alien")]


def test_valid_entries_not_quarantined():
    """Valid entries pass through normally — no quarantine."""
    store_a = _store("a")
    store_b = _store("b")

    store_a.add_node("n1", "entity", "Valid")
    store_a.add_node("n2", "signal", "Also valid")

    _sync_bidirectional(store_a, store_b)

    assert store_b.get_node("n1") is not None
    assert store_b.get_node("n2") is not None
    assert len(store_b.get_quarantined()) == 0


def test_local_writes_still_reject_invalid():
    """Local writes (add_node via API) still reject invalid ontology violations."""
    store = _store()
    with pytest.raises(ValueError):
        store.add_node("n1", "spaceship", "Invalid")


def test_quarantine_preserves_oplog_convergence():
    """Both peers have the same oplog size after sync, even with quarantine."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}, "ghost": {"properties": {}}},
        "edge_types": {"LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}}
    })
    store_a = GraphStore("a", extended)
    store_b = _store("b")

    store_a.add_node("n1", "entity", "Valid")
    store_a.add_node("n2", "ghost", "Quarantined on B")
    store_b.add_node("n3", "entity", "From B")

    _sync_bidirectional(store_a, store_b)

    # Both should have same oplog size (convergence)
    assert store_a.len() == store_b.len()

    # But different materialized graphs
    assert store_a.get_node("n2") is not None  # valid on A
    assert store_b.get_node("n2") is None  # quarantined on B


def test_quarantine_grows_only():
    """Quarantine is a grow-only set — entries never leave."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}, "phantom": {"properties": {}}},
        "edge_types": {}
    })
    store_a = GraphStore("a", extended)
    store_b = _store("b")

    store_a.add_node("n1", "phantom", "Invalid for B")

    _sync_bidirectional(store_a, store_b)

    q1 = len(store_b.get_quarantined())
    assert q1 > 0

    # Sync again — quarantine should not shrink
    _sync_bidirectional(store_a, store_b)
    q2 = len(store_b.get_quarantined())
    assert q2 >= q1


def test_invalid_edge_type_quarantined():
    """Entries with unknown edge types are quarantined."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}},
        "edge_types": {
            "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}},
            "HAUNTS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
        }
    })
    store_a = GraphStore("a", extended)
    store_b = _store("b")

    store_a.add_node("n1", "entity", "A")
    store_a.add_node("n2", "entity", "B")
    store_a.add_edge("e1", "LINKS", "n1", "n2")  # valid everywhere
    store_a.add_edge("e2", "HAUNTS", "n1", "n2")  # invalid on B

    _sync_bidirectional(store_a, store_b)

    assert store_b.get_edge("e1") is not None  # valid
    assert store_b.get_edge("e2") is None  # quarantined
    assert len(store_b.get_quarantined()) > 0


def test_get_quarantined_returns_hex_hashes():
    """get_quarantined() returns hex-encoded entry hashes."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}, "ufo": {"properties": {}}},
        "edge_types": {}
    })
    store_a = GraphStore("a", extended)
    store_b = _store("b")

    store_a.add_node("n1", "ufo", "Quarantined")

    _sync_bidirectional(store_a, store_b)

    quarantined = store_b.get_quarantined()
    assert len(quarantined) > 0
    for h in quarantined:
        assert isinstance(h, str)
        assert len(h) == 64  # 32 bytes = 64 hex chars
        assert all(c in "0123456789abcdef" for c in h)
