"""Tests for store.entries_affecting(id).

Validates Theorem 5 (Provenance Observation) and the CRDT-safety corollary
that depends on Theorem 4 (Composition of Clock and Existence Semilattices).

Covers the test matrix from the plan: present node, tombstoned node, edge
source/target lookup, edge id lookup, determinism across peers after sync,
causal ordering of returned entries.
"""

import json

from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string"},
                "status": {"value_type": "string"},
            }
        }
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        }
    }
})


def _store(instance_id: str = "peer-a") -> GraphStore:
    return GraphStore(instance_id, ONTOLOGY)


def _hashes(entries: list[dict]) -> list[str]:
    return [e["hash"] for e in entries]


# -- Basic cases ----------------------------------------------------------


def test_never_existed_returns_empty():
    """Theorem 5: id never referenced → empty result."""
    store = _store()
    assert store.entries_affecting("ghost-id") == []


def _op(entry: dict) -> str:
    return json.loads(entry["payload"])["op"]


def test_single_create_returns_one_entry():
    """Live node, single AddNode. Returns exactly one entry."""
    store = _store()
    store.add_node("n1", "entity", label="n1")
    result = store.entries_affecting("n1")
    assert len(result) == 1
    payload = json.loads(result[0]["payload"])
    assert payload["op"] == "add_node"
    assert payload["node_id"] == "n1"


def test_many_updates_return_in_topo_order():
    """Multiple updates to the same property return in topological order."""
    store = _store()
    store.add_node("n1", "entity", label="n1")
    store.update_property("n1", "name", "first")
    store.update_property("n1", "name", "second")
    store.update_property("n1", "name", "third")

    result = store.entries_affecting("n1")
    assert len(result) == 4
    kinds = [_op(e) for e in result]
    assert kinds[0] == "add_node"
    assert kinds[1:] == ["update_property"] * 3


def test_tombstoned_node_returns_create_and_remove():
    """Tombstoned node surfaces both the create and the remove."""
    store = _store()
    store.add_node("n1", "entity", label="n1")
    store.remove_node("n1")

    result = store.entries_affecting("n1")
    assert len(result) == 2
    kinds = {_op(e) for e in result}
    assert kinds == {"add_node", "remove_node"}


# -- Edge lookups ---------------------------------------------------------


def test_node_id_finds_edges_as_source_or_target():
    """Querying by node_id returns edges whose source_id or target_id match."""
    store = _store()
    store.add_node("a", "entity", label="a")
    store.add_node("b", "entity", label="b")
    store.add_edge("e1", "LINKS", "a", "b")

    for_a = _hashes(store.entries_affecting("a"))
    for_b = _hashes(store.entries_affecting("b"))
    edge_hash = _hashes(store.entries_affecting("e1"))[0]

    assert edge_hash in for_a, "edge with source=a should surface for 'a'"
    assert edge_hash in for_b, "edge with target=b should surface for 'b'"


def test_edge_id_lookup_returns_edge_ops_only():
    """Querying by edge_id returns AddEdge and RemoveEdge, not unrelated node ops."""
    store = _store()
    store.add_node("a", "entity", label="a")
    store.add_node("b", "entity", label="b")
    store.add_edge("e1", "LINKS", "a", "b")
    store.remove_edge("e1")

    result = store.entries_affecting("e1")
    kinds = {_op(e) for e in result}
    assert kinds == {"add_edge", "remove_edge"}
    assert len(result) == 2


# -- Quarantine -----------------------------------------------------------


def test_verdict_agnostic_returns_raw_entries():
    """The primitive returns raw entries without validation verdicts.
    Callers cross-reference get_quarantined() if they care about validation."""
    store = _store()
    store.add_node("n1", "entity", label="n1")
    store.update_property("n1", "name", "foo")

    result = store.entries_affecting("n1")
    assert len(result) == 2
    for e in result:
        # Every entry carries its hash; caller looks it up in get_quarantined()
        assert isinstance(e["hash"], str) and len(e["hash"]) == 64
    # No 'quarantined' field on the entry dict; that's the caller's job.
    assert all("quarantined" not in e for e in result)


# -- Determinism ----------------------------------------------------------


def test_determinism_after_bidirectional_sync():
    """After bidirectional sync, both peers return the same set of entries
    in the same topological order. Validates Theorem 5's CRDT-safety
    corollary — any function built on top inherits convergence."""
    a = _store("peer-a")
    b = _store("peer-b")

    a.add_node("n1", "entity", label="n1")
    a.update_property("n1", "name", "from-a")
    b.add_node("n2", "entity", label="n2")
    b.update_property("n2", "name", "from-b")

    # Bidirectional sync via the standard offer/payload dance.
    offer_a = a.generate_sync_offer()
    payload_b_to_a = b.receive_sync_offer(offer_a)
    a.merge_sync_payload(payload_b_to_a)

    offer_b = b.generate_sync_offer()
    payload_a_to_b = a.receive_sync_offer(offer_b)
    b.merge_sync_payload(payload_a_to_b)

    # Both peers now hold the same OpLog. entries_affecting must agree.
    assert _hashes(a.entries_affecting("n1")) == _hashes(b.entries_affecting("n1"))
    assert _hashes(a.entries_affecting("n2")) == _hashes(b.entries_affecting("n2"))


# -- Topological order invariant ------------------------------------------


def test_result_respects_causal_ordering():
    """Parents always precede children in the returned sequence."""
    store = _store()
    store.add_node("n1", "entity", label="n1")
    # Build a chain of updates; each depends causally on the previous head.
    for i in range(5):
        store.update_property("n1", "name", f"v{i}")

    result = store.entries_affecting("n1")
    result_hashes = {e["hash"] for e in result}
    seen: set[str] = set()
    for entry in result:
        for parent in entry["next"]:
            # If a parent is also in the affecting set, it must come first.
            if parent in result_hashes:
                assert parent in seen, (
                    f"parent {parent[:8]} appeared AFTER child {entry['hash'][:8]}"
                )
        seen.add(entry["hash"])
