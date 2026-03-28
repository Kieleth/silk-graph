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


# -- read_all (inspect without draining) --


def test_read_all_returns_dicts():
    """read_all() returns ops as Python dicts with 'op' key."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1", {"status": "active"})
    buf.update_property("s1", "status", "maintenance")

    ops = buf.read_all()
    assert len(ops) == 2
    assert ops[0]["op"] == "add_node"
    assert ops[0]["node_id"] == "s1"
    assert ops[0]["node_type"] == "server"
    assert ops[0]["properties"]["status"] == "active"
    assert ops[1]["op"] == "update_property"
    assert ops[1]["entity_id"] == "s1"
    assert ops[1]["key"] == "status"


def test_read_all_does_not_drain():
    """read_all() doesn't clear the buffer."""
    buf = OperationBuffer(_tmp_buffer())
    buf.add_node("s1", "server", "S1")

    _ = buf.read_all()
    assert len(buf) == 1  # still there


def test_read_all_empty():
    buf = OperationBuffer(_tmp_buffer())
    assert buf.read_all() == []


def test_read_all_boot_event_pattern():
    """Simulates the boot event buffering pattern for crash loop detection."""
    buf = OperationBuffer(_tmp_buffer())

    # Simulate 3 boot attempts
    import time
    for i in range(3):
        buf.add_node(
            f"boot-{i}", "signal", "boot attempt",
            {"wheel": "0.4.2", "timestamp_ms": int(time.time() * 1000)},
            subtype="boot_event",
        )

    # Read and filter — same as crash loop detector would do
    ops = buf.read_all()
    boot_events = [
        op for op in ops
        if op["op"] == "add_node" and op.get("subtype") == "boot_event"
    ]
    assert len(boot_events) == 3
    assert all(e["properties"]["wheel"] == "0.4.2" for e in boot_events)
