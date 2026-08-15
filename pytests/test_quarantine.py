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


# -- Inquisition H6: quarantined implies not materialized --


def _push(src, dst):
    """One-way sync: everything src has that dst lacks."""
    return dst.merge_sync_payload(src.receive_sync_offer(dst.generate_sync_offer()))


def assert_quarantine_disjoint(store):
    """Invariant: no hash is both reported quarantined and resolvable to a
    materialized entity. H6 — the set must be a function of the oplog, not of
    sync history."""
    node_ids = {n["node_id"] for n in store.all_nodes()}
    edge_ids = {e["edge_id"] for e in store.all_edges()}
    for h in store.get_quarantined():
        entry = store.get(h)
        assert entry is not None, f"quarantined hash {h[:8]} does not resolve to an entry"
        payload = json.loads(entry["payload"])
        if payload.get("op") == "add_node":
            assert payload["node_id"] not in node_ids, (
                f"{payload['node_id']} is reported quarantined AND materialized")
        elif payload.get("op") == "add_edge":
            assert payload["edge_id"] not in edge_ids, (
                f"{payload['edge_id']} is reported quarantined AND materialized")


def test_quarantine_cleared_when_entry_becomes_valid_incrementally():
    """H6: re-applying a quarantined entry under an evolved ontology must clear
    its quarantine record, not keep it alongside the materialized node."""
    extended = json.dumps({
        "node_types": {"entity": {"properties": {}}, "ufo": {"properties": {}}},
        "edge_types": {}
    })
    a = GraphStore("a", extended)
    b = _store("b")

    a.add_node("x1", "ufo", "Unknown type")
    _push(a, b)
    assert b.get_node("x1") is None
    assert len(b.get_quarantined()) == 1

    # Operator extends locally, then a later payload re-includes x1.
    b.extend_ontology({"node_types": {"ufo": {"properties": {}}}})
    a.add_node("x2", "ufo", "Second")
    _push(a, b)

    assert b.get_node("x1") is not None
    assert b.get_node("x2") is not None
    assert_quarantine_disjoint(b)
    assert len(b.get_quarantined()) == 0


def test_quarantine_set_equal_after_bidirectional_sync():
    """I-06 / S6: two peers with identical oplogs produce identical quarantine
    sets. The old test asserted a disjunction that passes when the sets are
    maximally different (one empty, one not).

    Scoped as I-06's proof is: peers sharing a genesis. The proof says both
    replay "against the same evolved ontology", which peers with divergent
    genesis ontologies never do — see PROOF.md, where the premise is now
    stated in the invariant and not only in its proof.
    """
    a = _store("a")
    b = _store("b")
    # The federation conflict: both teams add the same type name, differently.
    a.extend_ontology({"node_types": {"ufo": {"properties": {
        "wings": {"value_type": "int"}}}}})
    b.extend_ontology({"node_types": {"ufo": {"properties": {
        "rotors": {"value_type": "int"}}}}})

    _sync_bidirectional(a, b)

    assert sorted(a.get_quarantined()) == sorted(b.get_quarantined())
    # And the conflict is real: exactly one of the two extensions loses,
    # deterministically, on both peers.
    assert len(a.get_quarantined()) == 1


# -- Inquisition H2: remote UpdateProperty is validated --


def test_remote_update_property_violating_constraint_is_quarantined():
    """H2: a peer's UpdateProperty that violates a local constraint must not
    materialize. Previously the merge path skipped validation entirely."""
    loose = json.dumps({
        "node_types": {"s": {"properties": {"cpu": {"value_type": "int"}}}},
        "edge_types": {}
    })
    strict = json.dumps({
        "node_types": {
            "s": {"properties": {"cpu": {"value_type": "int",
                                         "constraints": {"max": 8}}}}
        },
        "edge_types": {}
    })
    a = GraphStore("a", loose)
    b = GraphStore("b", strict)

    a.add_node("n1", "s", "n1")
    a.update_property("n1", "cpu", 50)
    _push(a, b)

    assert b.get_node("n1")["properties"].get("cpu") != 50
    assert len(b.get_quarantined()) == 1
    assert_quarantine_disjoint(b)


def test_remote_update_property_wrong_type_is_quarantined():
    """H2: a declared-type mismatch from a peer must not land in the graph."""
    as_string = json.dumps({
        "node_types": {"s": {"properties": {"cpu": {"value_type": "string"}}}},
        "edge_types": {}
    })
    as_int = json.dumps({
        "node_types": {"s": {"properties": {"cpu": {"value_type": "int"}}}},
        "edge_types": {}
    })
    a = GraphStore("a", as_string)
    d = GraphStore("d", as_int)

    a.add_node("m1", "s", "m1")
    a.update_property("m1", "cpu", "not-a-number")
    _push(a, d)

    assert d.get_node("m1")["properties"].get("cpu") != "not-a-number"
    assert len(d.get_quarantined()) == 1


def test_edge_property_update_is_validated_on_both_paths():
    """H2: edge property updates skipped validate_property_update on every
    path, because the caller only looked up nodes."""
    ont = json.dumps({
        "node_types": {"a": {"properties": {}}},
        "edge_types": {"R": {"source_types": ["a"], "target_types": ["a"],
                             "properties": {"w": {"value_type": "int"}}}}
    })
    store = GraphStore("local", ont)
    store.add_node("n1", "a", "n1")
    store.add_node("n2", "a", "n2")
    store.add_edge("e1", "R", "n1", "n2")

    with pytest.raises(ValueError):
        store.update_property("e1", "w", "not-an-int")
