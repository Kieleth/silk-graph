"""D-026: Open Properties — the ontology is the floor, not the ceiling.

Tests verifying that unknown properties and unknown subtypes are accepted.
Known properties and required properties are still validated.
"""

import json
import pytest
from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {
        "person": {
            "properties": {
                "name": {"value_type": "string", "required": True},
                "age": {"value_type": "int", "required": False},
            },
            "subtypes": {
                "employee": {
                    "properties": {
                        "department": {"value_type": "string", "required": True},
                    }
                }
            }
        },
        "document": {
            "properties": {
                "title": {"value_type": "string", "required": True},
            }
        }
    },
    "edge_types": {
        "AUTHORED": {
            "source_types": ["person"],
            "target_types": ["document"],
            "properties": {}
        }
    }
})


def _store():
    return GraphStore("test-open", ONTOLOGY)


# -- Unknown properties accepted --


def test_unknown_property_on_node():
    """Extra properties not in the ontology are accepted and stored."""
    store = _store()
    store.add_node("alice", "person", "Alice", {
        "name": "Alice",
        "email": "alice@example.com",  # not in ontology
        "verified": True,              # not in ontology
    }, subtype="user")  # unknown subtype — also accepted (D-026)
    node = store.get_node("alice")
    assert node["properties"]["name"] == "Alice"
    assert node["properties"]["email"] == "alice@example.com"
    assert node["properties"]["verified"] is True


def test_unknown_property_on_edge():
    """Extra properties on edges are accepted and stored."""
    store = _store()
    store.add_node("alice", "person", "Alice", {"name": "Alice"}, subtype="user")
    store.add_node("doc1", "document", "Doc", {"title": "Paper"})
    store.add_edge("e1", "AUTHORED", "alice", "doc1", {
        "year": 2026,       # not in ontology
        "role": "primary",  # not in ontology
    })
    edge = store.get_edge("e1")
    assert edge["properties"]["year"] == 2026
    assert edge["properties"]["role"] == "primary"


def test_unknown_property_with_known_subtype():
    """Extra properties accepted even when using a known subtype."""
    store = _store()
    store.add_node("bob", "person", "Bob", {
        "name": "Bob",
        "department": "eng",      # required by employee subtype
        "slack_handle": "@bob",   # not in ontology
    }, subtype="employee")
    node = store.get_node("bob")
    assert node["properties"]["department"] == "eng"
    assert node["properties"]["slack_handle"] == "@bob"


def test_unknown_property_survives_sync():
    """Unknown properties survive sync between two stores."""
    store_a = GraphStore("a", ONTOLOGY)
    store_b = GraphStore("b", ONTOLOGY)

    store_a.add_node("alice", "person", "Alice", {
        "name": "Alice",
        "custom_field": "custom_value",
    }, subtype="user")

    # Sync A → B
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    node_b = store_b.get_node("alice")
    assert node_b is not None
    assert node_b["properties"]["custom_field"] == "custom_value"


def test_unknown_property_survives_persistence(tmp_path):
    """Unknown properties persist to disk and survive reload."""
    db_path = str(tmp_path / "test.redb")
    store = GraphStore("n1", ONTOLOGY, path=db_path)
    store.add_node("alice", "person", "Alice", {
        "name": "Alice",
        "custom": "persisted",
    }, subtype="user")
    del store

    store2 = GraphStore.open(db_path)
    node = store2.get_node("alice")
    assert node["properties"]["custom"] == "persisted"


# -- Unknown subtypes accepted --


def test_unknown_subtype_accepted():
    """Subtypes not in the ontology are accepted with type-level validation."""
    store = _store()
    store.add_node("carol", "person", "Carol", {
        "name": "Carol",  # required by person type — still enforced
    }, subtype="contractor")
    node = store.get_node("carol")
    assert node["subtype"] == "contractor"
    assert node["properties"]["name"] == "Carol"


def test_unknown_subtype_still_enforces_required():
    """Unknown subtype still requires type-level required properties."""
    store = _store()
    with pytest.raises(ValueError, match="name"):
        store.add_node("x", "person", "X", {}, subtype="contractor")


def test_subtype_on_type_without_subtypes():
    """Subtypes accepted even on types that don't declare any subtypes."""
    store = _store()
    store.add_node("doc1", "document", "Doc", {
        "title": "Paper",
    }, subtype="report")
    node = store.get_node("doc1")
    assert node["subtype"] == "report"


def test_unknown_subtype_with_extra_properties():
    """Unknown subtype + unknown properties both accepted together."""
    store = _store()
    store.add_node("dave", "person", "Dave", {
        "name": "Dave",
        "contract_end": "2026-12-31",  # not in ontology
        "rate": 150,                    # not in ontology
    }, subtype="freelancer")  # not in ontology
    node = store.get_node("dave")
    assert node["subtype"] == "freelancer"
    assert node["properties"]["contract_end"] == "2026-12-31"
    assert node["properties"]["rate"] == 150


# -- Known validation still works --


def test_required_property_still_enforced():
    """Required properties defined in the ontology are still enforced."""
    store = _store()
    with pytest.raises(ValueError, match="title"):
        store.add_node("x", "document", "X", {})  # missing required "title"


def test_known_property_type_still_enforced():
    """Type validation for known properties still works."""
    store = _store()
    with pytest.raises(ValueError, match="age"):
        store.add_node("x", "person", "X", {"name": "X", "age": "thirty"}, subtype="user")  # age should be int


def test_known_subtype_required_property_still_enforced():
    """Required properties for known subtypes are still enforced."""
    store = _store()
    with pytest.raises(ValueError, match="department"):
        store.add_node("x", "person", "X", {"name": "X"}, subtype="employee")  # missing "department"


def test_edge_type_constraints_still_enforced():
    """Edge grammar is still enforced — can't create invalid edges."""
    store = _store()
    store.add_node("alice", "person", "Alice", {"name": "Alice"}, subtype="user")
    store.add_node("bob", "person", "Bob", {"name": "Bob"}, subtype="user")
    with pytest.raises(ValueError):
        # AUTHORED requires person→document, not person→person
        store.add_edge("e1", "AUTHORED", "alice", "bob")


def test_unknown_node_type_still_rejected():
    """Unknown node types are still rejected (only properties/subtypes are open)."""
    store = _store()
    with pytest.raises(ValueError, match="node type"):
        store.add_node("x", "spaceship", "X", {})
