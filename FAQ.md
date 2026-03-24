# Silk FAQ

Common questions about silk-graph, rooted in real feedback from expert reviews.

---

### Why doesn't Silk have Dijkstra / PageRank / weighted graph algorithms?

Silk is a distributed sync layer for knowledge graphs, not a graph analytics engine. The built-in algorithms (BFS, shortest_path, impact_analysis, pattern_match) are navigation primitives — they answer "what's connected?" not "what's the optimal route?"

For analytics, use your preferred tool (NetworkX, igraph, graph-tool) on top of Silk's graph data. The intended architecture: multiple application instances on different servers, connected by Silk for consistency, each running analytics locally.

The `QueryEngine` extension protocol (R-07) is the explicit integration point:

```python
from silk import Query

class NetworkXEngine:
    def execute(self, store, query):
        import networkx as nx
        G = nx.DiGraph()
        for e in store.all_edges():
            G.add_edge(e["source_id"], e["target_id"], **e["properties"])
        # Parse query, run NetworkX algorithm, return results
        ...

results = Query(store, engine=NetworkXEngine()).raw("dijkstra(A, B, weight='latency')")
```

Note: `shortest_path()` is unweighted BFS (fewest hops, not minimum cost). This follows NetworkX naming convention — NetworkX's `shortest_path` also defaults to unweighted.

---

### Why no hyperedges / reification / named graphs?

Silk enforces **structural contracts** (what types exist, what connects to what, what properties are required). It does NOT enforce **semantic expressiveness** (reification, hyperedges, transitivity, cardinality). The boundary: Silk ensures the graph is well-formed. The application decides what the graph means.

To model "Bob claims (with 80% confidence) that Alice knows Carol," use the standard property graph pattern — an intermediate node:

```python
store.add_node("claim-1", "claim", "Bob's claim", {
    "confidence": 0.8,
    "asserted_by": "bob"
})
store.add_edge("e1", "SUBJECT", "claim-1", "alice")
store.add_edge("e2", "OBJECT", "claim-1", "carol")
```

This is the same pattern Neo4j, TigerGraph, and Amazon Neptune use. RDF's original reification model was widely considered a failure — RDF-star was invented to replace it.

Silk's ontology validates structure (`EMPLOYS` connects `organization→person`), not semantics (`EMPLOYS` is transitive). Structural guardrails prevent malformed graphs. Domain semantics are the application's responsibility.

Edge properties (D-026: open properties) can carry arbitrary metadata — `{"confidence": 0.8, "source": "bob", "timestamp": "2026-03-23"}` — without schema changes. Silk syncs whatever structure you build.
