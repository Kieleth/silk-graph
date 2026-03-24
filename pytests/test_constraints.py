"""Property constraints — enum, min, max validation at write time.

Tests for the extensible constraint system on PropertyDef.
"""

import pytest
from silk import GraphStore


def _store_with_constraints():
    """Store with enum and range constraints."""
    return GraphStore("test", {
        "node_types": {
            "server": {
                "properties": {
                    "status": {
                        "value_type": "string",
                        "required": True,
                        "constraints": {
                            "enum": ["active", "standby", "decommissioned"]
                        }
                    },
                    "port": {
                        "value_type": "int",
                        "constraints": {
                            "min": 1,
                            "max": 65535
                        }
                    },
                    "cpu_percent": {
                        "value_type": "float",
                        "constraints": {
                            "min": 0.0,
                            "max": 100.0
                        }
                    }
                }
            }
        },
        "edge_types": {}
    })


# -- Enum constraints --


def test_enum_valid_value():
    store = _store_with_constraints()
    store.add_node("s1", "server", "Prod", {"status": "active"})
    assert store.get_node("s1")["properties"]["status"] == "active"


def test_enum_all_values():
    store = _store_with_constraints()
    for status in ["active", "standby", "decommissioned"]:
        store.add_node(f"s-{status}", "server", status, {"status": status})
        assert store.get_node(f"s-{status}") is not None


def test_enum_invalid_rejected():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="enum"):
        store.add_node("s1", "server", "Bad", {"status": "exploded"})


def test_enum_case_sensitive():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="enum"):
        store.add_node("s1", "server", "Bad", {"status": "Active"})  # capital A


# -- Range constraints (int) --


def test_range_int_valid():
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 8080})
    assert store.get_node("s1")["properties"]["port"] == 8080


def test_range_int_min_boundary():
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 1})
    assert store.get_node("s1")["properties"]["port"] == 1


def test_range_int_max_boundary():
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 65535})
    assert store.get_node("s1")["properties"]["port"] == 65535


def test_range_int_below_min_rejected():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="min"):
        store.add_node("s1", "server", "S", {"status": "active", "port": 0})


def test_range_int_above_max_rejected():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="max"):
        store.add_node("s1", "server", "S", {"status": "active", "port": 70000})


# -- Range constraints (float) --


def test_range_float_valid():
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "cpu_percent": 45.5})
    assert store.get_node("s1")["properties"]["cpu_percent"] == 45.5


def test_range_float_below_min_rejected():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="min"):
        store.add_node("s1", "server", "S", {"status": "active", "cpu_percent": -1.0})


def test_range_float_above_max_rejected():
    store = _store_with_constraints()
    with pytest.raises(ValueError, match="max"):
        store.add_node("s1", "server", "S", {"status": "active", "cpu_percent": 101.0})


# -- No constraints (backward compat) --


def test_no_constraints_still_works():
    """Properties without constraints work as before."""
    store = GraphStore("test", {
        "node_types": {"entity": {"properties": {"name": {"value_type": "string"}}}},
        "edge_types": {}
    })
    store.add_node("n1", "entity", "Node", {"name": "anything goes"})
    assert store.get_node("n1") is not None


# -- Unknown constraints ignored (forward compat) --


def test_unknown_constraint_ignored():
    """Unknown constraint names are silently ignored."""
    store = GraphStore("test", {
        "node_types": {
            "entity": {
                "properties": {
                    "name": {
                        "value_type": "string",
                        "constraints": {"future_validator": "some_config"}
                    }
                }
            }
        },
        "edge_types": {}
    })
    store.add_node("n1", "entity", "Node", {"name": "works fine"})
    assert store.get_node("n1") is not None


# -- Constraints via extend_ontology --


def test_constraints_via_extension():
    """Constraints work on properties added via extend_ontology."""
    store = GraphStore("test", {
        "node_types": {"entity": {"properties": {}}},
        "edge_types": {}
    })
    store.extend_ontology({
        "node_type_updates": {
            "entity": {
                "add_properties": {
                    "priority": {
                        "value_type": "int",
                        "required": False,
                        "constraints": {"min": 1, "max": 5}
                    }
                }
            }
        }
    })
    store.add_node("n1", "entity", "Node", {"priority": 3})
    assert store.get_node("n1")["properties"]["priority"] == 3

    with pytest.raises(ValueError, match="max"):
        store.add_node("n2", "entity", "Bad", {"priority": 10})
