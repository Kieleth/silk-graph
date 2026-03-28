"""Tests for OperationBuffer — pre-store write-ahead buffer."""

import os
import tempfile

import pytest
from silk import GraphStore, OperationBuffer


def _tmp_buffer():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    os.unlink(path)  # start clean
    return path


def _store():
    return GraphStore("test", {
        "node_types": {
            "server": {"properties": {"status": {"value_type": "string"}}},
            "alert": {"properties": {"message": {"value_type": "string", "required": True}}},
        },
        "edge_types": {
            "MONITORS": {
                "source_types": ["alert"],
                "target_types": ["server"],
                "properties": {}
            }
        }
    })


# -- Basic operations --


def test_empty_buffer():
    buf = OperationBuffer(_tmp_buffer())
    assert len(buf) == 0


def test_buffer_add_node():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "Server 1", {"status": "active"})
    assert len(buf) == 1


def test_buffer_multiple_ops():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")
    buf.add_node("s2", "server", "S2")
    buf.update_property("s1", "status", "maintenance")
    buf.remove_node("s2")
    assert len(buf) == 4


def test_buffer_add_edge():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("a1", "alert", "Alert", {"message": "down"})
    buf.add_node("s1", "server", "S1")
    buf.add_edge("a1-MONITORS-s1", "MONITORS", "a1", "s1")
    assert len(buf) == 3


# -- Drain --


def test_drain_into_store():
    """Buffered ops become real graph nodes after drain."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "Server 1", {"status": "active"})
    buf.add_node("s2", "server", "Server 2")

    store = _store()
    count = buf.drain(store)

    assert count == 2
    assert store.get_node("s1") is not None
    assert store.get_node("s1")["properties"]["status"] == "active"
    assert store.get_node("s2") is not None


def test_drain_clears_buffer():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")

    store = _store()
    buf.drain(store)

    assert len(buf) == 0


def test_drain_fires_subscriptions():
    """Drained ops trigger D-023 subscriptions."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")

    store = _store()
    events = []
    store.subscribe(lambda e: events.append(e))

    buf.drain(store)

    assert len(events) == 1
    assert events[0]["op"] == "add_node"
    assert events[0]["node_id"] == "s1"


def test_drain_validates_ontology():
    """Drained ops are validated against the ontology."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("x", "nonexistent_type", "Bad")

    store = _store()
    with pytest.raises(ValueError, match="unknown node type"):
        buf.drain(store)


def test_drain_edges():
    """Edges drained after their endpoint nodes."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("a1", "alert", "Alert", {"message": "down"})
    buf.add_node("s1", "server", "S1")
    buf.add_edge("a1-MONITORS-s1", "MONITORS", "a1", "s1")

    store = _store()
    count = buf.drain(store)

    assert count == 3
    assert store.get_edge("a1-MONITORS-s1") is not None


def test_drain_update_property():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1", {"status": "booting"})
    buf.update_property("s1", "status", "active")

    store = _store()
    buf.drain(store)

    assert store.get_node("s1")["properties"]["status"] == "active"


def test_drain_remove_node():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")
    buf.remove_node("s1")

    store = _store()
    buf.drain(store)

    assert store.get_node("s1") is None  # tombstoned


def test_drain_remove_edge():
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("a1", "alert", "Alert", {"message": "x"})
    buf.add_node("s1", "server", "S1")
    buf.add_edge("a1-MONITORS-s1", "MONITORS", "a1", "s1")
    buf.remove_edge("a1-MONITORS-s1")

    store = _store()
    buf.drain(store)

    assert store.get_edge("a1-MONITORS-s1") is None  # tombstoned


# -- Persistence --


def test_buffer_survives_reopen():
    """Buffer file is persistent — new OperationBuffer reads old data."""
    path = _tmp_buffer()
    buf1 = OperationBuffer(path)
    buf1.add_node("s1", "server", "S1")

    buf2 = OperationBuffer(path)
    assert len(buf2) == 1


def test_buffer_path():
    path = _tmp_buffer()
    buf = OperationBuffer(path)
    assert buf.path == path


# -- Idempotence --


def test_drain_twice_is_noop():
    """Second drain on empty buffer applies nothing."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")

    store = _store()
    assert buf.drain(store) == 1
    assert buf.drain(store) == 0  # buffer was cleared
