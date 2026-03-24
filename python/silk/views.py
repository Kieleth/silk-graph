"""Graph views — filtered projections over a store or snapshot.

A view is a read-only filtered lens over the full graph. The oplog
is unchanged (CRDT convergence preserved). Queries only see the subset.

Usage:
    from silk.views import GraphView

    # See only server nodes and their edges
    view = GraphView(store, node_types=["server"])
    servers = view.all_nodes()  # only server nodes
    edges = view.all_edges()    # only edges where both endpoints are in the view

    # See only a subtype
    view = GraphView(store, subtypes=["router"])

    # Custom filter
    view = GraphView(store, predicate=lambda n: n["properties"].get("region") == "eu")
"""

from __future__ import annotations
from typing import Any, Callable


class GraphView:
    """Read-only filtered projection over a GraphStore or GraphSnapshot.

    Filters are applied to the materialized graph at query time.
    The underlying oplog is unchanged — CRDT convergence preserved.
    Edges are included only if BOTH endpoints pass the filter.
    """

    def __init__(
        self,
        store: Any,
        node_types: list[str] | None = None,
        subtypes: list[str] | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ):
        self._store = store
        self._node_types = set(node_types) if node_types else None
        self._subtypes = set(subtypes) if subtypes else None
        self._predicate = predicate

    def _node_passes(self, node: dict[str, Any]) -> bool:
        if self._node_types and node.get("node_type") not in self._node_types:
            return False
        if self._subtypes and node.get("subtype") not in self._subtypes:
            return False
        if self._predicate and not self._predicate(node):
            return False
        return True

    def all_nodes(self) -> list[dict[str, Any]]:
        return [n for n in self._store.all_nodes() if self._node_passes(n)]

    def all_edges(self) -> list[dict[str, Any]]:
        visible = {n["node_id"] for n in self.all_nodes()}
        return [
            e for e in self._store.all_edges()
            if e.get("source_id") in visible and e.get("target_id") in visible
        ]

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        node = self._store.get_node(node_id)
        if node and self._node_passes(node):
            return node
        return None

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        edge = self._store.get_edge(edge_id)
        if not edge:
            return None
        visible = {n["node_id"] for n in self.all_nodes()}
        if edge.get("source_id") in visible and edge.get("target_id") in visible:
            return edge
        return None

    def query_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        return [n for n in self._store.query_nodes_by_type(node_type) if self._node_passes(n)]

    def outgoing_edges(self, node_id: str) -> list[dict[str, Any]]:
        visible = {n["node_id"] for n in self.all_nodes()}
        if node_id not in visible:
            return []
        return [e for e in self._store.outgoing_edges(node_id) if e.get("target_id") in visible]

    def incoming_edges(self, node_id: str) -> list[dict[str, Any]]:
        visible = {n["node_id"] for n in self.all_nodes()}
        if node_id not in visible:
            return []
        return [e for e in self._store.incoming_edges(node_id) if e.get("source_id") in visible]

    def neighbors(self, node_id: str) -> list[str]:
        return [e.get("target_id", "") for e in self.outgoing_edges(node_id)]
