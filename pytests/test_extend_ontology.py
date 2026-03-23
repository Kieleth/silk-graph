"""R-03: Monotonic Ontology Evolution — extend the schema at runtime.

Tests verifying that the ontology can be extended with new types, properties,
and subtypes without recreating the store. Only additive changes allowed.
"""

import json
import pytest
from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string", "required": True},
                "status": {"value_type": "string", "required": True}
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
})


def _store(instance_id="test"):
    return GraphStore(instance_id, ONTOLOGY)


def _sync(a, b):
    for _ in range(2):
        offer = a.generate_sync_offer()
        payload = b.receive_sync_offer(offer)
        a.merge_sync_payload(payload)
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)


# -- Add new node types --


def test_add_new_node_type():
    """Extend ontology with a new node type, then use it."""
    store = _store()

    store.extend_ontology(json.dumps({
        "node_types": {
            "service": {
                "properties": {
                    "url": {"value_type": "string", "required": True}
                }
            }
        }
    }))

    # Now we can create service nodes
    store.add_node("svc-1", "service", "API Service", {"url": "https://api.example.com"})
    node = store.get_node("svc-1")
    assert node is not None
    assert node["node_type"] == "service"
    assert node["properties"]["url"] == "https://api.example.com"


def test_add_new_edge_type():
    """Extend ontology with a new edge type."""
    store = _store()

    store.extend_ontology(json.dumps({
        "edge_types": {
            "DEPENDS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {}
            }
        }
    }))

    store.add_node("n1", "entity", "A", {"name": "A", "status": "active"})
    store.add_node("n2", "entity", "B", {"name": "B", "status": "active"})
    store.add_edge("e1", "DEPENDS_ON", "n1", "n2")

    edge = store.get_edge("e1")
    assert edge is not None
    assert edge["edge_type"] == "DEPENDS_ON"


# -- Update existing types --


def test_add_property_to_existing_type():
    """Add a new optional property to an existing node type."""
    store = _store()

    store.extend_ontology(json.dumps({
        "node_type_updates": {
            "entity": {
                "add_properties": {
                    "region": {"value_type": "string", "required": False}
                }
            }
        }
    }))

    store.add_node("n1", "entity", "Server", {
        "name": "srv-1", "status": "active", "region": "eu-west"
    })
    node = store.get_node("n1")
    assert node["properties"]["region"] == "eu-west"


def test_relax_required_to_optional():
    """Relax a required property to optional."""
    store = _store()

    # Before: "status" is required
    with pytest.raises(ValueError):
        store.add_node("n1", "entity", "No status", {"name": "test"})

    # Extend: relax "status" to optional
    store.extend_ontology(json.dumps({
        "node_type_updates": {
            "entity": {
                "relax_properties": ["status"]
            }
        }
    }))

    # After: "status" is optional — works without it
    store.add_node("n2", "entity", "No status", {"name": "test"})
    assert store.get_node("n2") is not None


def test_add_subtype():
    """Add a new subtype to an existing node type."""
    store = _store()

    store.extend_ontology(json.dumps({
        "node_type_updates": {
            "entity": {
                "add_subtypes": {
                    "router": {
                        "properties": {
                            "ip": {"value_type": "string", "required": True}
                        }
                    }
                }
            }
        }
    }))

    store.add_node("r1", "entity", "Router 1", {
        "name": "gw-1", "status": "active", "ip": "10.0.0.1"
    }, subtype="router")
    node = store.get_node("r1")
    assert node["subtype"] == "router"
    assert node["properties"]["ip"] == "10.0.0.1"


# -- Monotonicity violations --


def test_reject_duplicate_node_type():
    """Cannot add a node type that already exists."""
    store = _store()
    with pytest.raises(ValueError, match="already exists"):
        store.extend_ontology(json.dumps({
            "node_types": {
                "entity": {"properties": {}}
            }
        }))


def test_reject_duplicate_property():
    """Cannot add a property that already exists on the type."""
    store = _store()
    with pytest.raises(ValueError, match="already"):
        store.extend_ontology(json.dumps({
            "node_type_updates": {
                "entity": {
                    "add_properties": {
                        "name": {"value_type": "string", "required": False}
                    }
                }
            }
        }))


def test_reject_update_unknown_type():
    """Cannot update a node type that doesn't exist."""
    store = _store()
    with pytest.raises(ValueError, match="spaceship"):
        store.extend_ontology(json.dumps({
            "node_type_updates": {
                "spaceship": {
                    "add_properties": {
                        "warp": {"value_type": "int"}
                    }
                }
            }
        }))


def test_reject_relax_unknown_property():
    """Cannot relax a property that doesn't exist."""
    store = _store()
    with pytest.raises(ValueError, match="nonexistent"):
        store.extend_ontology(json.dumps({
            "node_type_updates": {
                "entity": {
                    "relax_properties": ["nonexistent"]
                }
            }
        }))


# -- Sync with ontology evolution --


def test_extension_syncs_between_peers():
    """Ontology extension syncs to peers and they can use the new types."""
    store_a = _store("a")
    store_b = _store("b")

    # A extends ontology
    store_a.extend_ontology(json.dumps({
        "node_types": {
            "metric": {"properties": {"value": {"value_type": "float"}}}
        }
    }))
    store_a.add_node("m1", "metric", "CPU Load", {"value": 0.75})

    # Sync
    _sync(store_a, store_b)

    # B should have the extension AND the node
    assert store_b.get_node("m1") is not None
    assert store_b.get_node("m1")["node_type"] == "metric"


def test_concurrent_different_extensions_merge():
    """Two peers add different types independently — both exist after sync."""
    store_a = _store("a")
    store_b = _store("b")

    store_a.extend_ontology(json.dumps({
        "node_types": {"alpha_type": {"properties": {}}}
    }))
    store_a.add_node("a1", "alpha_type", "From A")

    store_b.extend_ontology(json.dumps({
        "node_types": {"beta_type": {"properties": {}}}
    }))
    store_b.add_node("b1", "beta_type", "From B")

    _sync(store_a, store_b)

    # Both peers should have both types and both nodes
    assert store_a.get_node("a1") is not None
    assert store_a.get_node("b1") is not None
    assert store_b.get_node("a1") is not None
    assert store_b.get_node("b1") is not None


def test_conflicting_extensions_quarantined():
    """Two peers add the same type name — second arrival is quarantined."""
    store_a = _store("a")
    store_b = _store("b")

    # Both add "conflicting_type" independently
    store_a.extend_ontology(json.dumps({
        "node_types": {"shared": {"properties": {"x": {"value_type": "int"}}}}
    }))
    store_b.extend_ontology(json.dumps({
        "node_types": {"shared": {"properties": {"y": {"value_type": "string"}}}}
    }))

    _sync(store_a, store_b)

    # One extension succeeds, the other is quarantined
    quarantined_a = store_a.get_quarantined()
    quarantined_b = store_b.get_quarantined()
    # At least one peer should quarantine the conflicting extension
    assert len(quarantined_a) > 0 or len(quarantined_b) > 0


def test_extension_persists_through_snapshot():
    """Ontology extensions survive snapshot roundtrip."""
    store_a = _store("a")

    store_a.extend_ontology(json.dumps({
        "node_types": {"ephemeral": {"properties": {}}}
    }))
    store_a.add_node("e1", "ephemeral", "Test")

    # Snapshot and restore
    snap = store_a.snapshot()
    store_b = GraphStore.from_snapshot("b", snap)

    assert store_b.get_node("e1") is not None
    assert store_b.get_node("e1")["node_type"] == "ephemeral"


# -- Edge cases --


def test_empty_extension_is_valid():
    """An extension with no changes is a no-op."""
    store = _store()
    store.extend_ontology(json.dumps({}))
    # No error, no change


def test_multiple_extensions_accumulate():
    """Multiple extensions stack — ontology grows monotonically."""
    store = _store()

    store.extend_ontology(json.dumps({
        "node_types": {"type_a": {"properties": {}}}
    }))
    store.extend_ontology(json.dumps({
        "node_types": {"type_b": {"properties": {}}}
    }))
    store.extend_ontology(json.dumps({
        "node_types": {"type_c": {"properties": {}}}
    }))

    store.add_node("a", "type_a", "A")
    store.add_node("b", "type_b", "B")
    store.add_node("c", "type_c", "C")

    assert store.get_node("a") is not None
    assert store.get_node("b") is not None
    assert store.get_node("c") is not None
