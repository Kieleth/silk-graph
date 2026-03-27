"""R-08: Epoch Compaction — compress the oplog into a checkpoint.

Tests verifying that compact() creates a checkpoint entry,
replaces the oplog, and preserves the full graph state.
"""

import pytest

import json
from silk import GraphStore

ONTOLOGY = {
    "node_types": {
        "entity": {
            "properties": {
                "status": {"value_type": "string"}
            }
        }
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {}
        }
    }
}


def _store(instance_id="test"):
    return GraphStore(instance_id, ONTOLOGY)


# -- Basic compaction --


def test_compact_returns_hash():
    """compact() returns a hex hash string."""
    store = _store()
    store.add_node("n1", "entity", "Node 1")
    h = store.compact()
    assert isinstance(h, str)
    assert len(h) == 64


def test_compact_reduces_oplog():
    """After compaction, oplog has exactly 1 entry (the checkpoint)."""
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_node("n3", "entity", "C")
    store.add_edge("e1", "LINKS", "n1", "n2")

    before = store.len()
    assert before >= 5  # genesis + 3 nodes + 1 edge

    store.compact()
    assert store.len() == 1  # just the checkpoint


def test_compact_preserves_nodes():
    """All live nodes survive compaction."""
    store = _store()
    store.add_node("n1", "entity", "A", {"status": "active"})
    store.add_node("n2", "entity", "B", {"status": "idle"})

    store.compact()

    assert store.get_node("n1") is not None
    assert store.get_node("n1")["properties"]["status"] == "active"
    assert store.get_node("n2") is not None
    assert store.get_node("n2")["properties"]["status"] == "idle"


def test_compact_preserves_edges():
    """All live edges survive compaction."""
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_edge("e1", "LINKS", "n1", "n2")

    store.compact()

    assert store.get_edge("e1") is not None
    assert store.get_edge("e1")["source_id"] == "n1"
    assert store.get_edge("e1")["target_id"] == "n2"


def test_compact_tombstoned_nodes_excluded():
    """Removed nodes are not in the checkpoint."""
    store = _store()
    store.add_node("n1", "entity", "Keep")
    store.add_node("n2", "entity", "Remove")
    store.remove_node("n2")

    store.compact()

    assert store.get_node("n1") is not None
    assert store.get_node("n2") is None
    assert len(store.all_nodes()) == 1


# -- Writing after compaction --


def test_write_after_compact():
    """New writes work after compaction."""
    store = _store()
    store.add_node("n1", "entity", "Before")
    store.compact()

    store.add_node("n2", "entity", "After")
    assert store.get_node("n1") is not None
    assert store.get_node("n2") is not None
    assert store.len() == 2  # checkpoint + new entry


def test_multiple_compactions():
    """Multiple compactions work correctly."""
    store = _store()
    store.add_node("n1", "entity", "A")
    store.compact()

    store.add_node("n2", "entity", "B")
    store.compact()

    store.add_node("n3", "entity", "C")
    store.compact()

    assert store.len() == 1
    assert len(store.all_nodes()) == 3


# -- Sync after compaction --


def test_snapshot_after_compact():
    """Snapshot from a compacted store works for bootstrapping."""
    store_a = _store("a")
    store_a.add_node("n1", "entity", "A")
    store_a.add_node("n2", "entity", "B")
    store_a.compact()

    snap = store_a.snapshot()
    store_b = GraphStore.from_snapshot("b", snap)

    assert store_b.get_node("n1") is not None
    assert store_b.get_node("n2") is not None


def test_sync_after_compact():
    """Sync works between compacted and non-compacted stores."""
    store_a = _store("a")
    store_b = _store("b")

    store_a.add_node("n1", "entity", "From A")
    store_a.compact()

    # Sync A→B via snapshot (compacted store can't do delta to a fresh peer)
    snap = store_a.snapshot()
    store_b = GraphStore.from_snapshot("b", snap)

    assert store_b.get_node("n1") is not None


# -- Ontology evolution + compaction --


def test_compact_preserves_ontology_extensions():
    """Ontology extensions survive compaction."""
    store = _store()
    store.extend_ontology({
        "node_types": {"service": {"properties": {}}}
    })
    store.add_node("svc-1", "service", "API")

    store.compact()

    assert store.get_node("svc-1") is not None
    # Can still create service nodes (ontology preserved)
    store.add_node("svc-2", "service", "Web")
    assert store.get_node("svc-2") is not None


# -- Query after compaction --


def test_query_builder_after_compact():
    """Query builder works on compacted stores."""
    from silk import Query

    store = _store()
    store.add_node("n1", "entity", "A", {"status": "active"})
    store.add_node("n2", "entity", "B", {"status": "idle"})
    store.compact()

    active = Query(store).nodes("entity").where(status="active").collect()
    assert len(active) == 1
    assert active[0]["node_id"] == "n1"


# -- Persistence --


def test_compact_persistent_store(tmp_path):
    """Compaction works with persistent (redb) stores."""
    path = str(tmp_path / "test.redb")
    store = GraphStore("test", ONTOLOGY, path=path)
    store.add_node("n1", "entity", "Persistent")
    store.add_node("n2", "entity", "Also persistent")

    before = store.len()
    store.compact()
    assert store.len() == 1
    del store

    # Reopen
    store2 = GraphStore.open(path)
    assert store2.get_node("n1") is not None
    assert store2.get_node("n2") is not None
    assert store2.len() == 1


# -- create_checkpoint (inspection) --


def test_create_checkpoint_returns_bytes():
    """create_checkpoint returns entry bytes without modifying the store."""
    store = _store()
    store.add_node("n1", "entity", "Node")

    before = store.len()
    checkpoint_bytes = store.create_checkpoint()

    assert isinstance(checkpoint_bytes, bytes)
    assert len(checkpoint_bytes) > 0
    assert store.len() == before  # store unchanged


# -- Compaction safety (P3) --


def test_compact_safe_no_peers():
    """No registered peers → compaction is trivially safe."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    safe, reasons = store.verify_compaction_safe()
    assert safe
    assert reasons == []
    # compact() should succeed
    store.compact()
    assert store.len() == 1


def test_compact_unsafe_with_unsynced_peer():
    """Registered peer that hasn't synced → compaction is unsafe."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    store.register_peer("remote-1", "tcp://remote:7701")
    # remote-1 has never synced (last_seen_ms = 0)
    safe, reasons = store.verify_compaction_safe()
    assert not safe
    assert len(reasons) == 1
    assert "remote-1" in reasons[0]
    assert "never synced" in reasons[0]


def test_compact_rejects_when_unsafe():
    """compact(safe=True) raises when peers haven't synced."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    store.register_peer("remote-1", "tcp://remote:7701")
    with pytest.raises(RuntimeError, match="compaction is unsafe"):
        store.compact()  # safe=True by default


def test_compact_force_bypasses_safety():
    """compact(safe=False) compacts even with unsynced peers."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    store.register_peer("remote-1", "tcp://remote:7701")
    # Force compaction despite unsynced peer
    store.compact(safe=False)
    assert store.len() == 1


def test_compact_safe_after_sync():
    """After recording sync with all peers, compaction is safe."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    store.register_peer("remote-1", "tcp://remote:7701")

    # Record sync — peer is now up to date
    store.record_sync("remote-1")

    safe, reasons = store.verify_compaction_safe()
    assert safe
    assert reasons == []
    store.compact()
    assert store.len() == 1


def test_compact_unsafe_partial_sync():
    """Some peers synced, some haven't → unsafe."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    store.register_peer("peer-a", "tcp://a:7701")
    store.register_peer("peer-b", "tcp://b:7701")

    store.record_sync("peer-a")
    # peer-b hasn't synced

    safe, reasons = store.verify_compaction_safe()
    assert not safe
    assert len(reasons) == 1
    assert "peer-b" in reasons[0]
