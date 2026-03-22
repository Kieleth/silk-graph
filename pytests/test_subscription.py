"""Python tests for Silk graph subscriptions (D-023).

Tests that store.subscribe(callback) fires on local writes and remote merges,
with correct event dict fields, multiple subscriber support, unsubscribe,
and error isolation.
"""

import json

import pytest

from silk import GraphStore


ONTOLOGY = json.dumps(
    {
        "node_types": {
            "entity": {
                "description": "A thing",
                "properties": {
                    "status": {"value_type": "string"},
                    "cpu": {"value_type": "float"},
                },
            },
            "signal": {
                "description": "An observation",
                "properties": {
                    "severity": {"value_type": "string", "required": True},
                },
            },
        },
        "edge_types": {
            "OBSERVES": {
                "source_types": ["signal"],
                "target_types": ["entity"],
                "properties": {},
            },
        },
    }
)


@pytest.fixture
def store():
    return GraphStore("test-instance", ONTOLOGY)


# -- Basic firing --


def test_subscription_fires_on_add_node(store):
    """subscribe callback invoked after add_node."""
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("s1", "entity", "Server 1", {"status": "alive"})
    assert len(events) == 1
    assert events[0]["op"] == "add_node"
    assert events[0]["node_id"] == "s1"


def test_subscription_fires_on_update_property(store):
    """subscribe callback invoked after update_property."""
    store.add_node("s1", "entity", "Server 1", {"status": "alive"})
    events = []
    store.subscribe(lambda e: events.append(e))
    store.update_property("s1", "status", "dead")
    assert len(events) == 1
    assert events[0]["op"] == "update_property"
    assert events[0]["entity_id"] == "s1"
    assert events[0]["key"] == "status"
    assert events[0]["value"] == "dead"


def test_subscription_fires_on_add_edge(store):
    """subscribe callback invoked after add_edge."""
    store.add_node("s1", "entity", "Server 1")
    store.add_node("sig1", "signal", "Alert", {"severity": "high"})
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_edge("e1", "OBSERVES", "sig1", "s1")
    assert len(events) == 1
    assert events[0]["op"] == "add_edge"
    assert events[0]["edge_id"] == "e1"
    assert events[0]["edge_type"] == "OBSERVES"
    assert events[0]["source_id"] == "sig1"
    assert events[0]["target_id"] == "s1"


def test_subscription_fires_on_remove_node(store):
    """subscribe callback invoked after remove_node."""
    store.add_node("s1", "entity", "Server 1")
    events = []
    store.subscribe(lambda e: events.append(e))
    store.remove_node("s1")
    assert len(events) == 1
    assert events[0]["op"] == "remove_node"
    assert events[0]["node_id"] == "s1"


def test_subscription_fires_on_remove_edge(store):
    """subscribe callback invoked after remove_edge."""
    store.add_node("s1", "entity", "Server 1")
    store.add_node("sig1", "signal", "Alert", {"severity": "high"})
    store.add_edge("e1", "OBSERVES", "sig1", "s1")
    events = []
    store.subscribe(lambda e: events.append(e))
    store.remove_edge("e1")
    assert len(events) == 1
    assert events[0]["op"] == "remove_edge"
    assert events[0]["edge_id"] == "e1"


# -- Event dict fields --


def test_event_fields_add_node(store):
    """add_node event has all expected fields."""
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("s1", "entity", "Server 1", {"status": "alive"})
    e = events[0]
    assert isinstance(e["hash"], str) and len(e["hash"]) == 64
    assert e["op"] == "add_node"
    assert e["node_id"] == "s1"
    assert e["node_type"] == "entity"
    assert e["author"] == "test-instance"
    assert isinstance(e["clock_time"], int) and e["clock_time"] > 0
    assert e["local"] is True


def test_event_fields_update_property(store):
    """update_property event has entity_id, key, value."""
    store.add_node("s1", "entity", "Server 1", {"status": "alive"})
    events = []
    store.subscribe(lambda e: events.append(e))
    store.update_property("s1", "cpu", 85.5)
    e = events[0]
    assert e["op"] == "update_property"
    assert e["entity_id"] == "s1"
    assert e["key"] == "cpu"
    assert e["value"] == 85.5
    assert e["local"] is True


def test_event_fields_add_edge(store):
    """add_edge event has edge_id, edge_type, source_id, target_id."""
    store.add_node("s1", "entity", "Server 1")
    store.add_node("sig1", "signal", "Alert", {"severity": "high"})
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_edge("e1", "OBSERVES", "sig1", "s1")
    e = events[0]
    assert e["op"] == "add_edge"
    assert e["edge_id"] == "e1"
    assert e["edge_type"] == "OBSERVES"
    assert e["source_id"] == "sig1"
    assert e["target_id"] == "s1"
    assert e["local"] is True


# -- Local vs remote --


def test_event_local_true_for_local_write(store):
    """local=True for direct writes."""
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("s1", "entity", "Server 1")
    assert events[0]["local"] is True


def test_event_local_false_for_merge(store):
    """local=False for entries received via sync merge."""
    store_b = GraphStore("inst-b", ONTOLOGY)
    store_b.add_node("s1", "entity", "Server 1", {"status": "alive"})

    events = []
    store.subscribe(lambda e: events.append(e))

    # Sync: B → A
    offer_a = store.generate_sync_offer()
    payload_for_a = store_b.receive_sync_offer(offer_a)
    store.merge_sync_payload(payload_for_a)

    # Should have received at least the add_node event
    add_events = [e for e in events if e["op"] == "add_node"]
    assert len(add_events) == 1
    assert add_events[0]["local"] is False
    assert add_events[0]["author"] == "inst-b"


# -- Multiple subscribers --


def test_multiple_subscribers_all_fire(store):
    """Two subscribers both receive the same event."""
    events_a = []
    events_b = []
    store.subscribe(lambda e: events_a.append(e))
    store.subscribe(lambda e: events_b.append(e))
    store.add_node("s1", "entity", "Server 1")
    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_a[0]["hash"] == events_b[0]["hash"]


# -- Unsubscribe --


def test_unsubscribe_stops_callbacks(store):
    """After unsubscribe, callback no longer fires."""
    events = []
    sub_id = store.subscribe(lambda e: events.append(e))
    store.add_node("s1", "entity", "Server 1")
    assert len(events) == 1
    store.unsubscribe(sub_id)
    store.add_node("s2", "entity", "Server 2")
    assert len(events) == 1  # no new event


# -- Error isolation --


def test_subscriber_error_does_not_block_write(store):
    """Exception in callback doesn't prevent the write from succeeding."""

    def bad_callback(event):
        raise RuntimeError("subscriber bug")

    store.subscribe(bad_callback)
    # This must not raise — the write succeeds despite the callback error
    store.add_node("s1", "entity", "Server 1")
    node = store.get_node("s1")
    assert node is not None
    assert node["node_id"] == "s1"


# -- Ordering --


def test_subscriber_receives_events_in_order(store):
    """Events arrive in append order."""
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("s1", "entity", "Server 1")
    store.add_node("s2", "entity", "Server 2")
    store.update_property("s1", "status", "dead")
    assert len(events) == 3
    assert events[0]["node_id"] == "s1"
    assert events[1]["node_id"] == "s2"
    assert events[2]["entity_id"] == "s1"
