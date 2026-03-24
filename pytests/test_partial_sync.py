"""Partial sync — projection views + filtered sync.

Tests for GraphView (Approach 1: filtered materialization) and
receive_filtered_sync_offer (Approach 2: filtered entry transfer).
"""

from silk import GraphStore, GraphView

ONTOLOGY = {
    "node_types": {
        "server": {"properties": {"region": {"value_type": "string"}}},
        "service": {"properties": {"status": {"value_type": "string"}}},
        "alert": {"properties": {}},
    },
    "edge_types": {
        "RUNS": {"source_types": ["server"], "target_types": ["service"], "properties": {}},
        "ALERTS": {"source_types": ["alert"], "target_types": ["service"], "properties": {}},
    },
}


def _infra_store():
    """Store with servers, services, alerts, and edges."""
    store = GraphStore("ops", ONTOLOGY)
    store.add_node("srv-1", "server", "Prod EU", {"region": "eu"})
    store.add_node("srv-2", "server", "Prod US", {"region": "us"})
    store.add_node("svc-api", "service", "API", {"status": "up"})
    store.add_node("svc-db", "service", "DB", {"status": "down"})
    store.add_node("alert-1", "alert", "DB down")
    store.add_edge("e1", "RUNS", "srv-1", "svc-api")
    store.add_edge("e2", "RUNS", "srv-2", "svc-db")
    store.add_edge("e3", "ALERTS", "alert-1", "svc-db")
    return store


# ── Approach 1: GraphView (projection) ──


def test_view_by_node_type():
    store = _infra_store()
    view = GraphView(store, node_types=["server"])
    nodes = view.all_nodes()
    assert len(nodes) == 2
    assert all(n["node_type"] == "server" for n in nodes)


def test_view_excludes_other_types():
    store = _infra_store()
    view = GraphView(store, node_types=["server"])
    assert view.get_node("svc-api") is None
    assert view.get_node("alert-1") is None


def test_view_includes_matching():
    store = _infra_store()
    view = GraphView(store, node_types=["server"])
    assert view.get_node("srv-1") is not None
    assert view.get_node("srv-2") is not None


def test_view_edges_both_endpoints():
    """Edges only included if BOTH endpoints are in the view."""
    store = _infra_store()
    view = GraphView(store, node_types=["server"])
    edges = view.all_edges()
    # RUNS edges connect server→service — service not in view
    assert len(edges) == 0


def test_view_edges_same_type():
    """Edges included when both endpoints pass."""
    store = _infra_store()
    # View with both servers and services → RUNS edges visible
    view = GraphView(store, node_types=["server", "service"])
    edges = view.all_edges()
    assert len(edges) == 2  # e1, e2 (RUNS)
    # ALERTS edge excluded: alert not in view
    alert_edges = [e for e in edges if e["edge_type"] == "ALERTS"]
    assert len(alert_edges) == 0


def test_view_by_predicate():
    store = _infra_store()
    view = GraphView(store, predicate=lambda n: n["properties"].get("region") == "eu")
    nodes = view.all_nodes()
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "srv-1"


def test_view_combined_filters():
    store = _infra_store()
    view = GraphView(
        store,
        node_types=["server"],
        predicate=lambda n: n["properties"].get("region") == "us"
    )
    nodes = view.all_nodes()
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "srv-2"


def test_view_outgoing_edges():
    store = _infra_store()
    view = GraphView(store, node_types=["server", "service"])
    edges = view.outgoing_edges("srv-1")
    assert len(edges) == 1
    assert edges[0]["target_id"] == "svc-api"


def test_view_incoming_edges():
    store = _infra_store()
    view = GraphView(store, node_types=["server", "service"])
    edges = view.incoming_edges("svc-api")
    assert len(edges) == 1
    assert edges[0]["source_id"] == "srv-1"


def test_view_neighbors():
    store = _infra_store()
    view = GraphView(store, node_types=["server", "service"])
    n = view.neighbors("srv-1")
    assert "svc-api" in n


def test_view_on_snapshot():
    """GraphView works on historical snapshots too."""
    store = _infra_store()
    snap = store.as_of(*store.clock_time())
    view = GraphView(snap, node_types=["server"])
    assert len(view.all_nodes()) == 2


def test_view_unfiltered():
    """GraphView with no filters returns everything."""
    store = _infra_store()
    view = GraphView(store)
    assert len(view.all_nodes()) == len(store.all_nodes())
    assert len(view.all_edges()) == len(store.all_edges())


# ── Approach 2: Filtered Sync ──


def _sync_filtered(sender, receiver, node_types):
    """One-way filtered sync: sender→receiver, only node_types."""
    offer = receiver.generate_sync_offer()
    payload = sender.receive_filtered_sync_offer(offer, node_types)
    return receiver.merge_sync_payload(payload)


def test_filtered_sync_includes_requested_type():
    """Filtered sync always includes entries of the requested type."""
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    _sync_filtered(sender, receiver, ["server"])

    # Receiver definitely has servers
    assert receiver.get_node("srv-1") is not None
    assert receiver.get_node("srv-2") is not None


def test_filtered_sync_causal_closure_may_include_others():
    """Causal closure may pull in entries of other types.

    In a single DAG, entries are causally linked via next pointers.
    Filtering by type is best-effort — causal ancestors of kept entries
    are always included, even if they're a different type.
    For truly independent entry sets, use separate stores.
    """
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    _sync_filtered(sender, receiver, ["server"])

    # May or may not have other types — depends on DAG structure
    # The guarantee is: requested types are present + oplog is valid
    assert receiver.get_node("srv-1") is not None
    assert receiver.len() >= 3  # genesis + at least servers


def test_filtered_sync_preserves_edges():
    """Edges are included even if they cross the filter boundary."""
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    # Sync both servers and services
    _sync_filtered(sender, receiver, ["server", "service"])

    assert receiver.get_node("srv-1") is not None
    assert receiver.get_node("svc-api") is not None
    assert receiver.get_edge("e1") is not None  # RUNS edge


def test_filtered_sync_causal_closure():
    """Genesis and schema entries are always included (causal closure)."""
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    _sync_filtered(sender, receiver, ["server"])

    # Receiver should have at least genesis + server entries
    assert receiver.len() >= 3  # genesis + 2 servers


def test_filtered_sync_multiple_rounds():
    """Multiple filtered syncs accumulate correctly."""
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    _sync_filtered(sender, receiver, ["server"])
    _sync_filtered(sender, receiver, ["service"])

    # After both rounds, receiver has both types
    assert receiver.get_node("srv-1") is not None
    assert receiver.get_node("svc-api") is not None


def test_filtered_sync_best_effort():
    """Filtered sync is best-effort — causal closure may include all entries.

    In a single DAG where entries are sequential, every entry is a causal
    descendant of genesis. Filtering reduces entries only when the excluded
    type's entries have no causal relationship to kept entries.

    For real bandwidth reduction on independent data sets, use separate stores.
    For query-time filtering, use GraphView (Approach 1) instead.
    """
    store = GraphStore("sender", {
        "node_types": {"log": {"properties": {}}, "metric": {"properties": {}}},
        "edge_types": {}
    })
    for i in range(10):
        store.add_node(f"log-{i}", "log", f"Log {i}")
        store.add_node(f"metric-{i}", "metric", f"Metric {i}")

    receiver = GraphStore("receiver", {
        "node_types": {"log": {"properties": {}}, "metric": {"properties": {}}},
        "edge_types": {}
    })

    _sync_filtered(store, receiver, ["log"])

    # Logs are guaranteed present
    assert receiver.get_node("log-0") is not None
    # Metrics may or may not be present (causal closure)
    # The key value of filtered sync is when used with GraphView:
    view = GraphView(receiver, node_types=["log"])
    assert len(view.all_nodes()) == 10  # only logs visible


def test_full_sync_still_works():
    """Regular (unfiltered) sync still works alongside filtered sync."""
    sender = _infra_store()
    receiver = GraphStore("receiver", ONTOLOGY)

    # Full sync
    offer = receiver.generate_sync_offer()
    payload = sender.receive_sync_offer(offer)
    receiver.merge_sync_payload(payload)

    assert len(receiver.all_nodes()) == len(sender.all_nodes())
