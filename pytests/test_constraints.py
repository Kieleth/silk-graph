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


# -- update_property validation --


def test_update_property_enum_valid():
    """update_property accepts valid enum values."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active"})
    store.update_property("s1", "status", "standby")
    assert store.get_node("s1")["properties"]["status"] == "standby"


def test_update_property_enum_invalid_rejected():
    """update_property rejects invalid enum values."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active"})
    with pytest.raises(ValueError, match="enum"):
        store.update_property("s1", "status", "exploded")


def test_update_property_range_valid():
    """update_property accepts values within range."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 8080})
    store.update_property("s1", "port", 443)
    assert store.get_node("s1")["properties"]["port"] == 443


def test_update_property_range_below_min_rejected():
    """update_property rejects values below min."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 8080})
    with pytest.raises(ValueError, match="min"):
        store.update_property("s1", "port", 0)


def test_update_property_range_above_max_rejected():
    """update_property rejects values above max."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 8080})
    with pytest.raises(ValueError, match="max"):
        store.update_property("s1", "port", 70000)


def test_update_property_wrong_type_rejected():
    """update_property rejects wrong value type."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "port": 8080})
    with pytest.raises(ValueError):
        store.update_property("s1", "port", "not_a_number")


def test_update_property_unknown_property_accepted():
    """update_property accepts unknown properties (D-026)."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active"})
    store.update_property("s1", "custom_field", "anything")
    assert store.get_node("s1")["properties"]["custom_field"] == "anything"


def test_update_property_float_range_rejected():
    """update_property rejects float values outside range."""
    store = _store_with_constraints()
    store.add_node("s1", "server", "S", {"status": "active", "cpu_percent": 50.0})
    with pytest.raises(ValueError, match="max"):
        store.update_property("s1", "cpu_percent", 101.0)


# -- Pattern constraint --


def _store_with_pattern():
    return GraphStore("test", {
        "node_types": {
            "project": {
                "properties": {
                    "slug": {
                        "value_type": "string",
                        "required": True,
                        "constraints": {"pattern": "^[a-z0-9-]+$"}
                    }
                }
            }
        },
        "edge_types": {}
    })


def test_pattern_valid():
    store = _store_with_pattern()
    store.add_node("p1", "project", "P", {"slug": "my-project-1"})
    assert store.get_node("p1")["properties"]["slug"] == "my-project-1"


def test_pattern_rejects_uppercase():
    store = _store_with_pattern()
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("p1", "project", "P", {"slug": "My-Project"})


def test_pattern_rejects_spaces():
    store = _store_with_pattern()
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("p1", "project", "P", {"slug": "has space"})


def test_pattern_rejects_underscore():
    store = _store_with_pattern()
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("p1", "project", "P", {"slug": "has_underscore"})


def test_pattern_update_property_rejected():
    store = _store_with_pattern()
    store.add_node("p1", "project", "P", {"slug": "valid-slug"})
    with pytest.raises(ValueError, match="pattern"):
        store.update_property("p1", "slug", "INVALID SLUG!")


def test_pattern_ip_address():
    """Full regex: IPv4 address pattern."""
    store = GraphStore("test", {
        "node_types": {
            "host": {
                "properties": {
                    "ip": {
                        "value_type": "string",
                        "constraints": {
                            "pattern": r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
                        }
                    }
                }
            }
        },
        "edge_types": {}
    })
    store.add_node("h1", "host", "H", {"ip": "192.168.1.100"})
    assert store.get_node("h1")["properties"]["ip"] == "192.168.1.100"
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("h2", "host", "H", {"ip": "not-an-ip"})


def test_pattern_email_like():
    """Full regex: email-like pattern with alternation and escapes."""
    store = GraphStore("test", {
        "node_types": {
            "contact": {
                "properties": {
                    "email": {
                        "value_type": "string",
                        "constraints": {
                            "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
                        }
                    }
                }
            }
        },
        "edge_types": {}
    })
    store.add_node("c1", "contact", "C", {"email": "user@example.com"})
    assert store.get_node("c1") is not None
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("c2", "contact", "C", {"email": "not-an-email"})


def test_pattern_invalid_regex_rejected():
    """Invalid regex pattern produces a clear error."""
    store = GraphStore("test", {
        "node_types": {
            "item": {
                "properties": {
                    "code": {
                        "value_type": "string",
                        "constraints": {"pattern": "[invalid("}
                    }
                }
            }
        },
        "edge_types": {}
    })
    with pytest.raises(ValueError, match="pattern"):
        store.add_node("n1", "item", "I", {"code": "anything"})


# -- String length constraints --


def _store_with_length():
    return GraphStore("test", {
        "node_types": {
            "item": {
                "properties": {
                    "name": {
                        "value_type": "string",
                        "constraints": {"min_length": 1, "max_length": 50}
                    }
                }
            }
        },
        "edge_types": {}
    })


def test_length_valid():
    store = _store_with_length()
    store.add_node("n1", "item", "I", {"name": "hello"})
    assert store.get_node("n1")["properties"]["name"] == "hello"


def test_length_min_boundary():
    store = _store_with_length()
    store.add_node("n1", "item", "I", {"name": "x"})
    assert store.get_node("n1") is not None


def test_length_max_boundary():
    store = _store_with_length()
    store.add_node("n1", "item", "I", {"name": "x" * 50})
    assert store.get_node("n1") is not None


def test_length_empty_rejected():
    store = _store_with_length()
    with pytest.raises(ValueError, match="min_length"):
        store.add_node("n1", "item", "I", {"name": ""})


def test_length_too_long_rejected():
    store = _store_with_length()
    with pytest.raises(ValueError, match="max_length"):
        store.add_node("n1", "item", "I", {"name": "x" * 51})


# -- Exclusive range constraints --


def _store_with_exclusive():
    return GraphStore("test", {
        "node_types": {
            "metric": {
                "properties": {
                    "score": {
                        "value_type": "float",
                        "constraints": {"min_exclusive": 0.0, "max_exclusive": 100.0}
                    }
                }
            }
        },
        "edge_types": {}
    })


def test_exclusive_valid():
    store = _store_with_exclusive()
    store.add_node("m1", "metric", "M", {"score": 50.0})
    assert store.get_node("m1")["properties"]["score"] == 50.0


def test_exclusive_min_boundary_rejected():
    """min_exclusive: 0.0 rejects exactly 0.0"""
    store = _store_with_exclusive()
    with pytest.raises(ValueError, match="min_exclusive"):
        store.add_node("m1", "metric", "M", {"score": 0.0})


def test_exclusive_max_boundary_rejected():
    """max_exclusive: 100.0 rejects exactly 100.0"""
    store = _store_with_exclusive()
    with pytest.raises(ValueError, match="max_exclusive"):
        store.add_node("m1", "metric", "M", {"score": 100.0})


def test_exclusive_just_above_min():
    store = _store_with_exclusive()
    store.add_node("m1", "metric", "M", {"score": 0.001})
    assert store.get_node("m1") is not None


def test_exclusive_just_below_max():
    store = _store_with_exclusive()
    store.add_node("m1", "metric", "M", {"score": 99.999})
    assert store.get_node("m1") is not None


def test_exclusive_int_also_works():
    """Exclusive bounds work on int values too."""
    store = GraphStore("test", {
        "node_types": {
            "item": {
                "properties": {
                    "priority": {
                        "value_type": "int",
                        "constraints": {"min_exclusive": 0, "max_exclusive": 10}
                    }
                }
            }
        },
        "edge_types": {}
    })
    store.add_node("n1", "item", "I", {"priority": 5})
    assert store.get_node("n1") is not None
    with pytest.raises(ValueError, match="min_exclusive"):
        store.add_node("n2", "item", "I", {"priority": 0})
    with pytest.raises(ValueError, match="max_exclusive"):
        store.add_node("n3", "item", "I", {"priority": 10})
