"""R-07: Query Builder — fluent graph queries over Silk stores.

Tests the Python-side query builder that composes existing Silk primitives.
Also tests the QueryEngine extension point.
"""

import json
from silk import GraphStore, GraphSnapshot, Query, QueryEngine

ONTOLOGY = {
    "node_types": {
        "server": {"properties": {"status": {"value_type": "string"}, "region": {"value_type": "string"}}},
        "service": {"properties": {"status": {"value_type": "string"}}},
    },
    "edge_types": {
        "RUNS": {"source_types": ["server"], "target_types": ["service"], "properties": {}},
        "DEPENDS_ON": {"source_types": ["service"], "target_types": ["service"], "properties": {}},
    },
}


def _populated_store():
    """Create a store with a small infrastructure graph."""
    store = GraphStore("test", ONTOLOGY)
    store.add_node("srv-1", "server", "Prod Server 1", {"status": "active", "region": "eu-west"})
    store.add_node("srv-2", "server", "Prod Server 2", {"status": "active", "region": "us-east"})
    store.add_node("srv-3", "server", "Staging", {"status": "standby", "region": "eu-west"})
    store.add_node("svc-api", "service", "API", {"status": "up"})
    store.add_node("svc-web", "service", "Web Frontend", {"status": "up"})
    store.add_node("svc-db", "service", "Database", {"status": "down"})
    store.add_edge("e1", "RUNS", "srv-1", "svc-api")
    store.add_edge("e2", "RUNS", "srv-1", "svc-web")
    store.add_edge("e3", "RUNS", "srv-2", "svc-db")
    store.add_edge("e4", "DEPENDS_ON", "svc-api", "svc-db")
    store.add_edge("e5", "DEPENDS_ON", "svc-web", "svc-api")
    return store


# -- Basic queries --


def test_nodes_by_type():
    store = _populated_store()
    servers = Query(store).nodes("server").collect()
    assert len(servers) == 3


def test_nodes_all():
    store = _populated_store()
    all_nodes = Query(store).nodes().collect()
    assert len(all_nodes) == 6


def test_where_single():
    store = _populated_store()
    active = Query(store).nodes("server").where(status="active").collect()
    assert len(active) == 2


def test_where_multiple():
    store = _populated_store()
    eu_active = Query(store).nodes("server").where(status="active", region="eu-west").collect()
    assert len(eu_active) == 1
    assert eu_active[0]["node_id"] == "srv-1"


def test_where_fn():
    store = _populated_store()
    eu = Query(store).nodes("server").where_fn(
        lambda n: n["properties"].get("region", "").startswith("eu")
    ).collect()
    assert len(eu) == 2


# -- Following edges --


def test_follow_outgoing():
    store = _populated_store()
    # Services running on srv-1
    services = Query(store).nodes("server").where(node_id="srv-1").follow("RUNS").collect()
    # where(node_id=...) won't work — node_id is a top-level key, not in properties
    # Use where_fn instead
    services = (
        Query(store).nodes("server")
        .where_fn(lambda n: n["node_id"] == "srv-1")
        .follow("RUNS")
        .collect()
    )
    ids = {s["node_id"] for s in services}
    assert ids == {"svc-api", "svc-web"}


def test_follow_all_edge_types():
    store = _populated_store()
    # All nodes connected to svc-api (RUNS incoming + DEPENDS_ON outgoing)
    connected = (
        Query(store).nodes("service")
        .where_fn(lambda n: n["node_id"] == "svc-api")
        .follow(direction="both")
        .collect()
    )
    ids = {n["node_id"] for n in connected}
    # svc-db (DEPENDS_ON target), svc-web (DEPENDS_ON source → svc-api is target, so incoming)
    # outgoing: svc-api → svc-db (DEPENDS_ON)
    # incoming: srv-1 → svc-api (RUNS), svc-web → svc-api (DEPENDS_ON)
    assert "svc-db" in ids  # outgoing DEPENDS_ON
    assert "srv-1" in ids   # incoming RUNS


def test_follow_chain():
    """Multi-hop: servers → services → downstream services."""
    store = _populated_store()
    downstream = (
        Query(store).nodes("server")
        .where_fn(lambda n: n["node_id"] == "srv-1")
        .follow("RUNS")
        .follow("DEPENDS_ON")
        .collect()
    )
    ids = {n["node_id"] for n in downstream}
    # srv-1 RUNS svc-api, svc-web. svc-api DEPENDS_ON svc-db. svc-web DEPENDS_ON svc-api.
    assert "svc-db" in ids or "svc-api" in ids


# -- The demo query: find down services on active servers --


def test_find_down_services_on_active_servers():
    """The motivating use case from the roadmap."""
    store = _populated_store()
    down_services = (
        Query(store)
        .nodes("server")
        .where(status="active")
        .follow("RUNS")
        .where(status="down")
        .collect()
    )
    assert len(down_services) == 1
    assert down_services[0]["node_id"] == "svc-db"


# -- Result methods --


def test_collect_ids():
    store = _populated_store()
    ids = Query(store).nodes("server").collect_ids()
    assert set(ids) == {"srv-1", "srv-2", "srv-3"}


def test_count():
    store = _populated_store()
    assert Query(store).nodes("service").count() == 3


def test_first():
    store = _populated_store()
    first = Query(store).nodes("server").first()
    assert first is not None
    assert first["node_type"] == "server"


def test_first_empty():
    store = _populated_store()
    first = Query(store).nodes("server").where(status="nonexistent").first()
    assert first is None


def test_limit():
    store = _populated_store()
    limited = Query(store).nodes().limit(2).collect()
    assert len(limited) == 2


def test_len_and_iter():
    store = _populated_store()
    q = Query(store).nodes("server")
    assert len(q) == 3
    ids = [n["node_id"] for n in q]
    assert len(ids) == 3


# -- Works with GraphSnapshot --


def test_query_on_snapshot():
    """Query builder works on historical snapshots too."""
    store = _populated_store()
    snap = store.as_of(*store.clock_time())
    servers = Query(snap).nodes("server").where(status="active").collect()
    assert len(servers) == 2


# -- Edge queries --


def test_edges_by_type():
    store = _populated_store()
    runs_edges = Query(store).edges("RUNS").collect()
    assert len(runs_edges) == 3


# -- Extension point --


def test_custom_engine():
    """QueryEngine protocol allows custom query engines."""
    class MockEngine:
        def execute(self, store, query):
            # Dummy: return all servers regardless of query
            return store.query_nodes_by_type("server")

    store = _populated_store()
    results = Query(store, engine=MockEngine()).raw("FIND ALL SERVERS")
    assert len(results) == 3


def test_raw_without_engine_raises():
    """raw() raises if no engine is registered."""
    store = _populated_store()
    import pytest
    with pytest.raises(RuntimeError, match="No query engine"):
        Query(store).raw("SELECT *")


def test_engine_protocol():
    """QueryEngine is a runtime-checkable Protocol."""
    class GoodEngine:
        def execute(self, store, query):
            return []

    class BadEngine:
        pass

    assert isinstance(GoodEngine(), QueryEngine)
    assert not isinstance(BadEngine(), QueryEngine)
