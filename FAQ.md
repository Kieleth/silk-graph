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

---

### How do I add enum or range validation to properties?

Use the `constraints` field on `PropertyDef`. Built-in constraints: `enum` (allowed values), `min`/`max` (numeric range).

```python
store = GraphStore("test", {
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
                    "constraints": {"min": 1, "max": 65535}
                }
            }
        }
    },
    "edge_types": {}
})

store.add_node("s1", "server", "Prod", {"status": "active", "port": 8080})   # OK
store.add_node("s2", "server", "Bad", {"status": "exploded"})                 # ValueError!
store.add_node("s3", "server", "Bad", {"port": 0})                           # ValueError!
```

Constraints are validated at write time. Unknown constraint names are silently ignored (forward compatibility).

### How do I add a custom constraint type?

Add a new handler to `validate_constraints()` in `src/ontology.rs`. The function receives the constraint config (as `serde_json::Value`) and the property value. Return `Ok(())` or `Err(ConstraintViolation)`.

Example — adding a `pattern` (regex) constraint:

```rust
// In validate_constraints(), add after the "max" handler:
if let Some(serde_json::Value::String(pattern)) = constraints.get("pattern") {
    if let Value::String(s) = value {
        let re = regex::Regex::new(pattern).map_err(|e| ...)?;
        if !re.is_match(s) {
            return Err(ValidationError::ConstraintViolation { ... });
        }
    }
}
```

Then users can write:
```json
{"value_type": "string", "constraints": {"pattern": "^[a-z0-9-]+$"}}
```

Contributions welcome — open a PR with your constraint type + tests.

### Why no cardinality constraints ("a team has 2-10 members")?

Cardinality requires counting edges during validation, which means the validator needs access to the full graph — not just the property value. The current validation API (`validate_node(type, subtype, properties)`) doesn't have graph context.

This is a different API contract. If you need cardinality validation, implement it in your application layer by checking edge counts after writes. If there's demand, file an RFC — it would require a design change to the validation pipeline.

### Why no OWL-style reasoning (transitivity, inverse properties)?

Silk enforces structural contracts — "EMPLOYS connects organization→person." It does NOT infer new facts — "if A employs B and B manages C, then A indirectly employs C." That's a reasoner's job (Pellet, HermiT, Protégé).

Silk's design choice: validate at write time, don't reason. This keeps the engine fast and the behavior predictable. If you need reasoning, run a reasoner on top of Silk's graph data.
