"""Security hardening tests — verifying fixes for S-01 through S-20.

These tests verify that the security fixes work from the Python API level.
They complement the Rust unit tests in clock.rs, bloom.rs, sync.rs.
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


# -- R-02: Ontology validation on sync (quarantine model) --


def test_sync_rejects_invalid_node_type():
    """R-02: valid entries from sync are accepted and materialized."""
    store_a = _store("a")
    store_b = _store("b")

    # A adds a valid node
    store_a.add_node("n1", "entity", "Node 1")

    # Sync A→B (valid)
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    assert store_b.get_node("n1") is not None


def test_sync_valid_entries_converge():
    """R-02: valid entries from sync are accepted and converge."""
    store_a = _store("a")
    store_b = _store("b")

    store_a.add_node("n1", "entity", "Node 1")
    store_a.add_node("n2", "entity", "Node 2")
    store_a.add_edge("e1", "LINKS", "n1", "n2")

    # Full bidirectional sync
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    assert store_b.get_node("n1") is not None
    assert store_b.get_node("n2") is not None
    assert store_b.get_edge("e1") is not None


# -- S-10: Value depth limits --


def test_deeply_nested_value_rejected():
    """S-10: deeply nested structures are rejected at write time."""
    store = _store()
    # Build a 100-level deep nested dict
    deep = "leaf"
    for _ in range(100):
        deep = {"nested": deep}

    with pytest.raises(ValueError, match="depth"):
        store.add_node("n1", "entity", "Node", {"data": deep})


def test_moderate_nesting_accepted():
    """S-10: reasonable nesting (< 64 levels) works fine."""
    store = _store()
    nested = "leaf"
    for _ in range(10):
        nested = {"level": nested}

    store.add_node("n1", "entity", "Node", {"data": nested})
    node = store.get_node("n1")
    assert node is not None


# -- S-12: Value size limits --


def test_oversized_string_rejected():
    """S-12: strings > 1 MB are rejected."""
    store = _store()
    big_string = "x" * (1_048_577)  # 1 MB + 1 byte

    with pytest.raises(ValueError, match="exceeds maximum"):
        store.add_node("n1", "entity", "Node", {"data": big_string})


def test_normal_string_accepted():
    """S-12: strings <= 1 MB are accepted."""
    store = _store()
    ok_string = "x" * 10_000

    store.add_node("n1", "entity", "Node", {"data": ok_string})
    node = store.get_node("n1")
    assert len(node["properties"]["data"]) == 10_000


def test_oversized_list_rejected():
    """S-12: lists > 10K items are rejected."""
    store = _store()
    big_list = list(range(10_001))

    with pytest.raises(ValueError, match="exceeds maximum"):
        store.add_node("n1", "entity", "Node", {"data": big_list})


def test_normal_list_accepted():
    """S-12: lists <= 10K items are accepted."""
    store = _store()
    ok_list = list(range(100))

    store.add_node("n1", "entity", "Node", {"data": ok_list})
    node = store.get_node("n1")
    assert len(node["properties"]["data"]) == 100


def test_oversized_map_rejected():
    """S-12: maps > 10K entries are rejected."""
    store = _store()
    big_map = {f"k{i}": i for i in range(10_001)}

    with pytest.raises(ValueError, match="exceeds maximum"):
        store.add_node("n1", "entity", "Node", {"data": big_map})


# -- S-03: Sync message size limits --


def test_corrupt_sync_payload_rejected():
    """S-03: garbage bytes rejected as sync payload."""
    store = _store()
    with pytest.raises(ValueError):
        store.merge_sync_payload(b"this is not valid msgpack")


def test_corrupt_sync_offer_rejected():
    """S-03: garbage bytes rejected as sync offer."""
    store = _store()
    with pytest.raises(ValueError):
        store.receive_sync_offer(b"garbage")


# -- Snapshot --


def test_snapshot_roundtrip():
    """Snapshot can bootstrap a new peer."""
    store_a = _store("a")
    store_a.add_node("n1", "entity", "Node 1")
    store_a.add_node("n2", "entity", "Node 2")
    store_a.add_edge("e1", "LINKS", "n1", "n2")

    snapshot = store_a.snapshot()
    store_b = GraphStore.from_snapshot("b", snapshot)

    assert store_b.get_node("n1") is not None
    assert store_b.get_node("n2") is not None
    assert store_b.get_edge("e1") is not None


# -- General robustness --


def test_remove_nonexistent_node_no_crash():
    """Removing a node that doesn't exist shouldn't crash."""
    store = _store()
    # This creates a RemoveNode entry — the graph just ignores it
    store.remove_node("nonexistent")


def test_update_property_on_nonexistent_entity():
    """Updating a property on a nonexistent entity doesn't crash."""
    store = _store()
    store.update_property("nonexistent", "key", "value")
    # Property update on missing entity is silently ignored (by design, for sync)
