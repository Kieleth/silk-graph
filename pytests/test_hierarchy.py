"""Tests for RDFS-level class hierarchy (Step 2).

parent_type enables type inheritance: queries, edge validation,
and property validation are all hierarchy-aware.
"""

import pytest
from silk import GraphStore


def _hierarchy_store():
    """thing → entity → server, thing → event"""
    return GraphStore("test", {
        "node_types": {
            "thing": {
                "properties": {
                    "name": {"value_type": "string", "required": True}
                }
            },
            "entity": {
                "parent_type": "thing",
                "properties": {
                    "status": {"value_type": "string"}
                }
            },
            "server": {
                "parent_type": "entity",
                "properties": {
                    "ip": {"value_type": "string"}
                }
            },
            "event": {
                "parent_type": "thing",
                "properties": {}
            },
        },
        "edge_types": {
            "RELATES_TO": {
                "source_types": ["thing"],
                "target_types": ["entity"],
                "properties": {}
            }
        }
    })


# -- Property inheritance --


def test_child_inherits_parent_required():
    """Server requires 'name' from grandparent thing."""
    store = _hierarchy_store()
    with pytest.raises(ValueError, match="requires property"):
        store.add_node("s1", "server", "S1")  # missing name


def test_child_with_inherited_property():
    """Server provides 'name' (from thing) and works."""
    store = _hierarchy_store()
    store.add_node("s1", "server", "S1", {"name": "web-01"})
    assert store.get_node("s1")["properties"]["name"] == "web-01"


def test_child_can_use_own_and_inherited():
    """Server can set name (inherited), status (from entity), ip (own)."""
    store = _hierarchy_store()
    store.add_node("s1", "server", "S1", {
        "name": "web-01", "status": "active", "ip": "10.0.0.1"
    })
    node = store.get_node("s1")
    assert node["properties"]["name"] == "web-01"
    assert node["properties"]["status"] == "active"
    assert node["properties"]["ip"] == "10.0.0.1"


# -- Hierarchy-aware queries --


def test_query_parent_returns_children():
    """query_nodes_by_type('thing') returns thing, entity, server, event nodes."""
    store = _hierarchy_store()
    store.add_node("t1", "thing", "T", {"name": "root"})
    store.add_node("e1", "entity", "E", {"name": "ent"})
    store.add_node("s1", "server", "S", {"name": "srv"})
    store.add_node("ev1", "event", "Ev", {"name": "evt"})

    things = store.query_nodes_by_type("thing")
    ids = {n["node_id"] for n in things}
    assert ids == {"t1", "e1", "s1", "ev1"}


def test_query_mid_level_returns_descendants():
    """query_nodes_by_type('entity') returns entity + server, not event."""
    store = _hierarchy_store()
    store.add_node("e1", "entity", "E", {"name": "ent"})
    store.add_node("s1", "server", "S", {"name": "srv"})
    store.add_node("ev1", "event", "Ev", {"name": "evt"})

    entities = store.query_nodes_by_type("entity")
    ids = {n["node_id"] for n in entities}
    assert ids == {"e1", "s1"}


def test_query_leaf_returns_only_self():
    """query_nodes_by_type('server') returns only servers."""
    store = _hierarchy_store()
    store.add_node("s1", "server", "S", {"name": "srv"})
    store.add_node("e1", "entity", "E", {"name": "ent"})

    servers = store.query_nodes_by_type("server")
    assert len(servers) == 1
    assert servers[0]["node_id"] == "s1"


# -- Hierarchy-aware edge validation --


def test_edge_accepts_descendant_as_source():
    """RELATES_TO source=thing accepts server (server is-a thing)."""
    store = _hierarchy_store()
    store.add_node("s1", "server", "S", {"name": "srv"})
    store.add_node("e1", "entity", "E", {"name": "ent"})
    store.add_edge("edge1", "RELATES_TO", "s1", "e1")
    assert store.get_edge("edge1") is not None


def test_edge_accepts_descendant_as_target():
    """RELATES_TO target=entity accepts server (server is-a entity)."""
    store = _hierarchy_store()
    store.add_node("t1", "thing", "T", {"name": "root"})
    store.add_node("s1", "server", "S", {"name": "srv"})
    store.add_edge("edge1", "RELATES_TO", "t1", "s1")
    assert store.get_edge("edge1") is not None


def test_edge_rejects_wrong_branch():
    """RELATES_TO target=entity rejects event (event is NOT entity)."""
    store = _hierarchy_store()
    store.add_node("t1", "thing", "T", {"name": "root"})
    store.add_node("ev1", "event", "Ev", {"name": "evt"})
    with pytest.raises(ValueError, match="cannot have target type"):
        store.add_edge("edge1", "RELATES_TO", "t1", "ev1")


# -- Backward compatibility --


def test_no_parent_type_works_as_before():
    """Types without parent_type are unaffected."""
    store = GraphStore("test", {
        "node_types": {
            "widget": {"properties": {"color": {"value_type": "string"}}}
        },
        "edge_types": {}
    })
    store.add_node("w1", "widget", "W", {"color": "red"})
    assert store.get_node("w1") is not None


def test_parent_type_survives_sync():
    """Hierarchy works after snapshot + bootstrap."""
    a = _hierarchy_store()
    a.add_node("s1", "server", "S", {"name": "srv"})
    a.add_node("e1", "entity", "E", {"name": "ent"})

    snap = a.snapshot()
    b = GraphStore.from_snapshot("inst-b", snap)

    # Hierarchy-aware query on bootstrapped store
    things = b.query_nodes_by_type("thing")
    ids = {n["node_id"] for n in things}
    assert "s1" in ids
    assert "e1" in ids


def test_dangling_parent_rejected():
    """Ontology with parent_type pointing to nonexistent type is rejected."""
    with pytest.raises(ValueError):
        GraphStore("test", {
            "node_types": {
                "orphan": {
                    "parent_type": "ghost",
                    "properties": {}
                }
            },
            "edge_types": {}
        })
