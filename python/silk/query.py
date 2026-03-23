"""R-07: Query Builder — fluent graph queries over Silk stores.

A Python-native query API that composes existing Silk primitives
(query_nodes_by_type, outgoing_edges, get_node, property access)
into readable, chainable queries.

This is the foundation layer. It covers ~90% of query use cases.
For advanced needs (Datalog, SPARQL, Cypher), implement the
QueryEngine protocol and register it with the store.

Example:
    from silk.query import Query

    # Find all down services running on active servers
    results = (
        Query(store)
        .nodes("server")
        .where(status="active")
        .follow("RUNS")
        .where(status="down")
        .collect()
    )

Extension point:
    from silk.query import QueryEngine

    class DatalogEngine(QueryEngine):
        def execute(self, store, query_str):
            # Parse Datalog, evaluate, return results
            ...

    store_query = Query(store, engine=DatalogEngine())
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEngine(Protocol):
    """Extension point for custom query engines (Datalog, SPARQL, etc).

    Implement this protocol and pass to Query(store, engine=my_engine).
    The execute() method receives the store and a query string,
    and returns a list of result dicts.
    """

    def execute(self, store: Any, query: str) -> list[dict[str, Any]]:
        """Execute a query string against a store. Returns list of result dicts."""
        ...


class Query:
    """Fluent query builder over a Silk GraphStore or GraphSnapshot.

    Chains operations: select nodes → filter by property → follow edges →
    filter again → collect results. Each step narrows the working set.

    Works with both GraphStore (live) and GraphSnapshot (historical).
    """

    def __init__(self, store: Any, engine: QueryEngine | None = None):
        """Create a query builder.

        Args:
            store: A GraphStore or GraphSnapshot instance.
            engine: Optional custom query engine for string-based queries.
        """
        self._store = store
        self._engine = engine
        self._working_set: list[dict[str, Any]] | None = None
        self._steps: list[tuple[str, Any]] = []

    def raw(self, query_str: str) -> list[dict[str, Any]]:
        """Execute a raw query string via the registered engine.

        Requires a QueryEngine to be set. This is the extension point
        for Datalog, SPARQL, or other query languages.

        Raises:
            RuntimeError: If no engine is registered.
        """
        if self._engine is None:
            raise RuntimeError(
                "No query engine registered. Pass engine= to Query(), "
                "or use the fluent API (.nodes(), .where(), .follow())."
            )
        return self._engine.execute(self._store, query_str)

    def nodes(self, node_type: str | None = None, subtype: str | None = None) -> Query:
        """Start with nodes of a given type/subtype, or all nodes.

        Args:
            node_type: Filter by node type. None = all nodes.
            subtype: Filter by subtype. None = no subtype filter.
        """
        if node_type is not None:
            working = self._store.query_nodes_by_type(node_type)
        else:
            working = self._store.all_nodes()

        if subtype is not None:
            working = [n for n in working if n.get("subtype") == subtype]

        self._working_set = working
        return self

    def edges(self, edge_type: str | None = None) -> Query:
        """Start with edges, optionally filtered by type."""
        all_edges = self._store.all_edges()
        if edge_type is not None:
            all_edges = [e for e in all_edges if e.get("edge_type") == edge_type]
        self._working_set = all_edges
        return self

    def where(self, **kwargs: Any) -> Query:
        """Filter the working set by property values.

        Example:
            .where(status="active", region="eu-west")
            # Keeps only items where properties match ALL conditions.
        """
        if self._working_set is None:
            raise RuntimeError("Call .nodes() or .edges() before .where()")

        filtered = []
        for item in self._working_set:
            props = item.get("properties", {})
            if all(props.get(k) == v for k, v in kwargs.items()):
                filtered.append(item)

        self._working_set = filtered
        return self

    def where_fn(self, predicate: Any) -> Query:
        """Filter the working set by a custom predicate function.

        Example:
            .where_fn(lambda n: n["properties"].get("cpu", 0) > 80)
        """
        if self._working_set is None:
            raise RuntimeError("Call .nodes() or .edges() before .where_fn()")
        self._working_set = [item for item in self._working_set if predicate(item)]
        return self

    def follow(self, edge_type: str | None = None, direction: str = "out") -> Query:
        """Follow edges from the current node set to connected nodes.

        Args:
            edge_type: Only follow edges of this type. None = all edges.
            direction: "out" (outgoing), "in" (incoming), or "both".

        Replaces the working set with the target nodes.
        """
        if self._working_set is None:
            raise RuntimeError("Call .nodes() before .follow()")

        targets = []
        seen = set()

        for node in self._working_set:
            node_id = node.get("node_id")
            if node_id is None:
                continue

            # Collect (edge, other_end_id) pairs
            edge_pairs: list[tuple[dict, str]] = []
            if direction in ("out", "both"):
                for e in self._store.outgoing_edges(node_id):
                    edge_pairs.append((e, e.get("target_id", "")))
            if direction in ("in", "both"):
                for e in self._store.incoming_edges(node_id):
                    edge_pairs.append((e, e.get("source_id", "")))

            for edge, target_id in edge_pairs:
                if edge_type is not None and edge.get("edge_type") != edge_type:
                    continue
                if target_id and target_id not in seen:
                    target = self._store.get_node(target_id)
                    if target is not None:
                        targets.append(target)
                        seen.add(target_id)

        self._working_set = targets
        return self

    def limit(self, n: int) -> Query:
        """Keep only the first N results."""
        if self._working_set is not None:
            self._working_set = self._working_set[:n]
        return self

    def collect(self) -> list[dict[str, Any]]:
        """Return the current working set as a list of dicts."""
        return self._working_set or []

    def collect_ids(self) -> list[str]:
        """Return just the IDs from the current working set."""
        if self._working_set is None:
            return []
        return [
            item.get("node_id") or item.get("edge_id") or ""
            for item in self._working_set
        ]

    def count(self) -> int:
        """Return the count of items in the current working set."""
        return len(self._working_set) if self._working_set is not None else 0

    def first(self) -> dict[str, Any] | None:
        """Return the first item, or None."""
        if self._working_set and len(self._working_set) > 0:
            return self._working_set[0]
        return None

    def __len__(self) -> int:
        return self.count()

    def __iter__(self):
        return iter(self._working_set or [])
