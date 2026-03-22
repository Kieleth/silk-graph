"""Tests for Silk subtype support (D-024).

Subtypes allow coarse node types (e.g., 5 primitives) with per-subtype
property definitions. Edge constraints reference top-level types only.
Backward compatible: types without subtypes work exactly as before.
"""

import json
import pytest
from silk import GraphStore


# -- Ontology fixtures --


def _ontology_with_subtypes():
    """5-primitive-style ontology with subtypes."""
    return {
        "node_types": {
            "entity": {
                "subtypes": {
                    "server": {
                        "properties": {
                            "hostname": {"value_type": "string", "required": True},
                            "status": {"value_type": "string"},
                        }
                    },
                    "project": {
                        "properties": {
                            "name": {"value_type": "string", "required": True},
                            "slug": {"value_type": "string", "required": True},
                        }
                    },
                }
            },
            "signal": {
                "subtypes": {
                    "alert": {
                        "properties": {
                            "severity": {"value_type": "string", "required": True},
                            "message": {"value_type": "string"},
                        }
                    },
                }
            },
            "rule": {
                "properties": {
                    "name": {"value_type": "string", "required": True},
                }
            },
        },
        "edge_types": {
            "OBSERVES": {
                "source_types": ["signal"],
                "target_types": ["entity"],
                "properties": {},
            },
            "GUARDS": {
                "source_types": ["rule"],
                "target_types": ["entity"],
                "properties": {},
            },
        },
    }


def _make_store(ontology=None):
    if ontology is None:
        ontology = _ontology_with_subtypes()
    return GraphStore("test", json.dumps(ontology))


# -- Basic subtype operations --


def test_add_node_with_valid_subtype():
    """add_node with a valid subtype succeeds."""
    store = _make_store()
    store.add_node("srv-1", "entity", "server-1", {"hostname": "web01"}, subtype="server")
    node = store.get_node("srv-1")
    assert node is not None
    assert node["node_type"] == "entity"
    assert node["subtype"] == "server"
    assert node["properties"]["hostname"] == "web01"


def test_add_node_different_subtypes():
    """Different subtypes of the same type can coexist."""
    store = _make_store()
    store.add_node("srv-1", "entity", "srv", {"hostname": "web01"}, subtype="server")
    store.add_node("proj-1", "entity", "proj", {"name": "api", "slug": "api"}, subtype="project")
    srv = store.get_node("srv-1")
    proj = store.get_node("proj-1")
    assert srv["subtype"] == "server"
    assert proj["subtype"] == "project"


def test_get_node_returns_subtype():
    """get_node dict includes subtype field."""
    store = _make_store()
    store.add_node("srv-1", "entity", "srv", {"hostname": "web01"}, subtype="server")
    node = store.get_node("srv-1")
    assert "subtype" in node
    assert node["subtype"] == "server"


def test_get_node_no_subtype_when_type_has_no_subtypes():
    """For types without subtypes, get_node returns subtype as None."""
    store = _make_store()
    store.add_node("r1", "rule", "rule-1", {"name": "cpu-limit"})
    node = store.get_node("r1")
    assert node["subtype"] is None


# -- Validation: subtype required when subtypes defined --


def test_missing_subtype_when_required():
    """add_node without subtype on a type that has subtypes → error."""
    store = _make_store()
    with pytest.raises(ValueError, match="subtype"):
        store.add_node("srv-1", "entity", "srv", {"hostname": "web01"})


def test_unknown_subtype_accepted():
    """D-026: add_node with an unknown subtype succeeds (type-level validation only)."""
    store = _make_store()
    store.add_node("x", "entity", "x", {"hostname": "h"}, subtype="nonexistent")
    node = store.get_node("x")
    assert node is not None
    assert node["subtype"] == "nonexistent"


# -- Per-subtype property validation --


def test_subtype_required_properties_enforced():
    """Required properties for the specific subtype are enforced."""
    store = _make_store()
    # server requires hostname
    with pytest.raises(ValueError, match="hostname"):
        store.add_node("srv-1", "entity", "srv", {}, subtype="server")


def test_subtype_required_properties_different_per_subtype():
    """Different subtypes have different required properties."""
    store = _make_store()
    # project requires name + slug, not hostname
    with pytest.raises(ValueError, match="name"):
        store.add_node("proj-1", "entity", "proj", {}, subtype="project")
    # server requires hostname, not name/slug
    with pytest.raises(ValueError, match="hostname"):
        store.add_node("srv-1", "entity", "srv", {"name": "x"}, subtype="server")


def test_subtype_property_type_validation():
    """Property types are validated per subtype."""
    store = _make_store()
    with pytest.raises(ValueError):
        store.add_node("srv-1", "entity", "srv", {"hostname": 123}, subtype="server")


def test_subtype_unknown_property_accepted():
    """D-026: unknown properties are accepted without validation."""
    store = _make_store()
    store.add_node("srv-1", "entity", "srv", {"hostname": "h", "bogus": "x"}, subtype="server")
    node = store.get_node("srv-1")
    assert node["properties"]["bogus"] == "x"


# -- Backward compatibility: types without subtypes --


def test_type_without_subtypes_works_as_before():
    """Types that don't define subtypes work exactly as before."""
    store = _make_store()
    store.add_node("r1", "rule", "rule-1", {"name": "cpu-limit"})
    node = store.get_node("r1")
    assert node["node_type"] == "rule"
    assert node["properties"]["name"] == "cpu-limit"


def test_type_without_subtypes_accepts_subtype_arg():
    """D-026: subtypes accepted even on types that don't declare any."""
    store = _make_store()
    store.add_node("r1", "rule", "rule-1", {"name": "x"}, subtype="guardrail")
    node = store.get_node("r1")
    assert node["subtype"] == "guardrail"


def test_type_without_subtypes_validates_properties():
    """Property validation still works for types without subtypes."""
    store = _make_store()
    with pytest.raises(ValueError, match="name"):
        store.add_node("r1", "rule", "rule-1", {})


# -- Edge validation uses top-level types --


def test_edge_uses_top_level_type_not_subtype():
    """Edge validation references top-level type, works across subtypes."""
    store = _make_store()
    store.add_node("a1", "signal", "alert", {"severity": "high"}, subtype="alert")
    store.add_node("srv-1", "entity", "srv", {"hostname": "web01"}, subtype="server")
    store.add_node("proj-1", "entity", "proj", {"name": "api", "slug": "api"}, subtype="project")
    # OBSERVES: signal → entity — should work regardless of subtype
    store.add_edge("e1", "OBSERVES", "a1", "srv-1")
    store.add_edge("e2", "OBSERVES", "a1", "proj-1")


def test_edge_rejects_wrong_top_level_type():
    """Edge validation rejects wrong top-level types, ignoring subtypes."""
    store = _make_store()
    store.add_node("srv-1", "entity", "srv", {"hostname": "web01"}, subtype="server")
    store.add_node("srv-2", "entity", "srv2", {"hostname": "web02"}, subtype="server")
    # OBSERVES requires source=signal, not entity
    with pytest.raises(ValueError, match="OBSERVES"):
        store.add_edge("e1", "OBSERVES", "srv-1", "srv-2")


# -- Query --


def test_query_by_type_returns_all_subtypes():
    """query_nodes_by_type returns all nodes of that type, regardless of subtype."""
    store = _make_store()
    store.add_node("srv-1", "entity", "srv", {"hostname": "h1"}, subtype="server")
    store.add_node("proj-1", "entity", "proj", {"name": "a", "slug": "a"}, subtype="project")
    nodes = store.query_nodes_by_type("entity")
    assert len(nodes) == 2
    subtypes = {n["subtype"] for n in nodes}
    assert subtypes == {"server", "project"}


# -- Sync preserves subtype --


def test_sync_preserves_subtype():
    """Subtype survives sync between two stores via snapshot."""
    ontology = json.dumps(_ontology_with_subtypes())
    store_a = GraphStore("a", ontology)
    store_a.add_node("srv-1", "entity", "srv", {"hostname": "h1"}, subtype="server")
    # Snapshot A → bootstrap B
    snap = store_a.snapshot()
    store_b = GraphStore.from_snapshot("b", snap)
    node = store_b.get_node("srv-1")
    assert node is not None
    assert node["subtype"] == "server"


# -- Subscription events include subtype --


def test_subscription_event_includes_subtype():
    """Subscription events for add_node include the subtype."""
    store = _make_store()
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("srv-1", "entity", "srv", {"hostname": "h1"}, subtype="server")
    assert len(events) == 1
    assert events[0]["op"] == "add_node"
    assert events[0]["node_type"] == "entity"
    assert events[0]["subtype"] == "server"


def test_subscription_event_no_subtype_when_none():
    """Subscription events for types without subtypes have subtype=None or absent."""
    store = _make_store()
    events = []
    store.subscribe(lambda e: events.append(e))
    store.add_node("r1", "rule", "rule-1", {"name": "x"})
    assert len(events) == 1
    # subtype should be None or not present
    assert events[0].get("subtype") is None
