"""R-06: Time-Travel Queries — look at the graph at any point in the past.

Tests verifying that store.as_of(physical_ms, logical) returns a read-only
GraphSnapshot with the correct historical state.
"""

import json
import time
import pytest
from silk import GraphStore, GraphSnapshot

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


# -- Basic time-travel --


def test_as_of_returns_snapshot():
    """as_of returns a GraphSnapshot object."""
    store = _store()
    ct = store.clock_time()
    snap = store.as_of(ct[0], ct[1])
    assert isinstance(snap, GraphSnapshot)


def test_as_of_captures_state_at_time():
    """Snapshot at an earlier time doesn't include later writes."""
    store = _store()
    store.add_node("n1", "entity", "First")
    t_after_first = store.clock_time()

    time.sleep(0.01)
    store.add_node("n2", "entity", "Second")

    # Snapshot after first but before second
    snap = store.as_of(t_after_first[0], t_after_first[1])
    assert snap.get_node("n1") is not None
    assert snap.get_node("n2") is None


def test_as_of_current_time_matches_live():
    """Snapshot at current time matches the live graph."""
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_edge("e1", "LINKS", "n1", "n2")

    ct = store.clock_time()
    snap = store.as_of(ct[0], ct[1])

    assert snap.get_node("n1") is not None
    assert snap.get_node("n2") is not None
    assert snap.get_edge("e1") is not None
    assert len(snap.all_nodes()) == len(store.all_nodes())
    assert len(snap.all_edges()) == len(store.all_edges())


def test_as_of_before_genesis():
    """Snapshot before genesis has no nodes or edges."""
    store = _store()
    store.add_node("n1", "entity", "Node")

    snap = store.as_of(1000, 0)  # year 1970
    assert len(snap.all_nodes()) == 0
    assert len(snap.all_edges()) == 0


# -- LWW and add-wins semantics --


def test_as_of_property_update():
    """Snapshot before a property update shows the old value."""
    store = _store()
    store.add_node("n1", "entity", "Node", {"status": "active"})
    t_before_update = store.clock_time()

    time.sleep(0.01)
    store.update_property("n1", "status", "inactive")

    snap_before = store.as_of(t_before_update[0], t_before_update[1])
    node = snap_before.get_node("n1")
    assert node["properties"]["status"] == "active"

    snap_after = store.as_of(*store.clock_time())
    node = snap_after.get_node("n1")
    assert node["properties"]["status"] == "inactive"


def test_as_of_remove_and_resurrect():
    """Snapshot respects tombstones and add-wins resurrection."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    t_alive = store.clock_time()

    time.sleep(0.01)
    store.remove_node("n1")
    t_dead = store.clock_time()

    time.sleep(0.01)
    store.add_node("n1", "entity", "Resurrected")

    # When alive
    assert store.as_of(t_alive[0], t_alive[1]).get_node("n1") is not None
    # When dead
    assert store.as_of(t_dead[0], t_dead[1]).get_node("n1") is None
    # After resurrection
    assert store.as_of(*store.clock_time()).get_node("n1") is not None


# -- Snapshot is read-only --


def test_snapshot_has_no_mutation_methods():
    """GraphSnapshot has query methods but no mutations."""
    store = _store()
    snap = store.as_of(*store.clock_time())

    assert not hasattr(snap, "add_node")
    assert not hasattr(snap, "add_edge")
    assert not hasattr(snap, "update_property")
    assert not hasattr(snap, "remove_node")
    assert not hasattr(snap, "remove_edge")
    assert not hasattr(snap, "extend_ontology")
    assert not hasattr(snap, "merge_sync_payload")


# -- Snapshot metadata --


def test_snapshot_metadata():
    """Snapshot exposes cutoff clock and instance ID."""
    store = _store("my-instance")
    snap = store.as_of(123456789, 42)
    assert snap.cutoff_clock() == (123456789, 42)
    assert snap.instance_id() == "my-instance"


# -- Query methods on snapshot --


def test_snapshot_query_by_type():
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    snap = store.as_of(*store.clock_time())
    assert len(snap.query_nodes_by_type("entity")) == 2


def test_snapshot_outgoing_incoming():
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_edge("e1", "LINKS", "n1", "n2")
    snap = store.as_of(*store.clock_time())
    assert len(snap.outgoing_edges("n1")) == 1
    assert len(snap.incoming_edges("n2")) == 1


def test_snapshot_bfs():
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_node("n3", "entity", "C")
    store.add_edge("e1", "LINKS", "n1", "n2")
    store.add_edge("e2", "LINKS", "n2", "n3")
    snap = store.as_of(*store.clock_time())
    reachable = snap.bfs("n1")
    assert "n2" in reachable
    assert "n3" in reachable


def test_snapshot_shortest_path():
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_edge("e1", "LINKS", "n1", "n2")
    snap = store.as_of(*store.clock_time())
    path = snap.shortest_path("n1", "n2")
    assert path is not None
    assert "n1" in path and "n2" in path


def test_snapshot_neighbors():
    store = _store()
    store.add_node("n1", "entity", "A")
    store.add_node("n2", "entity", "B")
    store.add_edge("e1", "LINKS", "n1", "n2")
    snap = store.as_of(*store.clock_time())
    assert "n2" in snap.neighbors("n1")


# -- Multiple snapshots --


def test_multiple_snapshots_independent():
    """Two snapshots at different times are independent."""
    store = _store()
    store.add_node("n1", "entity", "First")
    t1 = store.clock_time()

    time.sleep(0.01)
    store.add_node("n2", "entity", "Second")
    t2 = store.clock_time()

    snap1 = store.as_of(t1[0], t1[1])
    snap2 = store.as_of(t2[0], t2[1])

    assert len(snap1.all_nodes()) == 1
    assert len(snap2.all_nodes()) == 2


def test_snapshot_does_not_affect_live_store():
    """Creating a snapshot doesn't modify the live store."""
    store = _store()
    store.add_node("n1", "entity", "Node")
    node_count_before = len(store.all_nodes())

    _ = store.as_of(*store.clock_time())
    _ = store.as_of(1000, 0)

    assert len(store.all_nodes()) == node_count_before
