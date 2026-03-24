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
