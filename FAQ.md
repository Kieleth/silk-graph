# Silk FAQ

Answers to real questions from expert reviews and early users.

> **Quick links:** [README](README.md) · [WHY](WHY.md) · [DESIGN](DESIGN.md) · [PROOF](PROOF.md) · [PROTOCOL](PROTOCOL.md) · [SECURITY](SECURITY.md) · [ROADMAP](ROADMAP.md)

---

## Architecture & Scope

### Why doesn't Silk have Dijkstra / PageRank / weighted graph algorithms?

Silk is a distributed sync layer, not a graph analytics engine. The built-in algorithms (`bfs`, `shortest_path`, `impact_analysis`, `pattern_match`) are navigation primitives — they answer "what's connected?" not "what's the optimal route?"

For analytics, use your preferred tool on top of Silk's graph data. The intended architecture: multiple application instances connected by Silk for consistency, each running analytics locally.

The [`QueryEngine`](QUERY_EXTENSIONS.md) extension protocol is the integration point:

```python
from silk import Query

class NetworkXEngine:
    def execute(self, store, query):
        import networkx as nx
        G = nx.DiGraph()
        for e in store.all_edges():
            G.add_edge(e["source_id"], e["target_id"], **e["properties"])
        # Parse query, run algorithm, return results
        ...

results = Query(store, engine=NetworkXEngine()).raw("dijkstra(A, B, weight='latency')")
```

> **Note:** `shortest_path()` is unweighted BFS (fewest hops, not minimum cost). Same default as NetworkX.

---

### Why no hyperedges / reification / named graphs?

Silk enforces **structural contracts** (types, connections, required properties), not **semantic expressiveness** (reification, hyperedges, transitivity). The boundary: Silk ensures the graph is well-formed. The application decides what the graph means.

Model n-ary relationships with intermediate nodes — the industry standard for property graphs (Neo4j, TigerGraph, Amazon Neptune all do this):

```python
store.add_node("claim-1", "claim", "Bob's claim", {
    "confidence": 0.8,
    "asserted_by": "bob"
})
store.add_edge("e1", "SUBJECT", "claim-1", "alice")
store.add_edge("e2", "OBJECT", "claim-1", "carol")
```

Edge properties ([D-026: open properties](https://github.com/Kieleth/silk-graph/blob/main/DESIGN.md)) carry arbitrary metadata without schema changes: `{"confidence": 0.8, "source": "bob"}`.

---

### Why no OWL-style reasoning (transitivity, inverse properties)?

Silk validates at write time. It does not infer new facts.

- **Structural** (Silk does this): `EMPLOYS` connects `organization→person` — validated
- **Semantic** (application does this): `EMPLOYS` is transitive — inference, not validation

If you need reasoning, run a reasoner (Pellet, HermiT) on top of Silk's graph data. Silk stays fast and predictable.

---

## Schema & Constraints

### How do I add enum or range validation to properties?

Use the `constraints` field on property definitions:

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

store.add_node("s1", "server", "Prod", {"status": "active", "port": 8080})  # OK
store.add_node("s2", "server", "Bad", {"status": "exploded"})                # ValueError!
store.add_node("s3", "server", "Bad", {"port": 0})                          # ValueError!
```

Built-in: `enum` (allowed string values), `min`/`max` (numeric range). Unknown constraint names are silently ignored (forward compatibility for community-contributed validators).

---

### How do I add a custom constraint type?

Add a handler to `validate_constraints()` in [`src/ontology.rs`](https://github.com/Kieleth/silk-graph/blob/main/src/ontology.rs). Example — adding `pattern` (regex):

```rust
if let Some(serde_json::Value::String(pattern)) = constraints.get("pattern") {
    if let Value::String(s) = value {
        let re = regex::Regex::new(pattern).map_err(|e| ...)?;
        if !re.is_match(s) {
            return Err(ValidationError::ConstraintViolation { ... });
        }
    }
}
```

Users then write: `{"value_type": "string", "constraints": {"pattern": "^[a-z0-9-]+$"}}`.

Contributions welcome — open a PR with your constraint type + tests.

---

### Why no cardinality constraints ("a team has 2-10 members")?

Cardinality requires counting edges during validation — the validator needs graph context, not just the property value. The current API (`validate_node(type, subtype, properties)`) doesn't have graph access.

Workaround: check edge counts in your application after writes. If there's demand, [file an RFC](https://github.com/Kieleth/silk-graph/issues) — it requires a design change to the validation pipeline.

---

## Compaction & Growth

### Won't the oplog grow forever?

No. Use compaction policies. `store.compact()` compresses the oplog into a single checkpoint — all live data preserved, tombstones removed.

```python
from silk import ThresholdPolicy, IntervalPolicy

# Compact when oplog exceeds 1000 entries
policy = ThresholdPolicy(max_entries=1000)
policy.check(store)  # call after write batches

# Compact at most once per hour
policy = IntervalPolicy(seconds=3600)
policy.check(store)  # call on a timer

# Custom policy
class MyPolicy:
    def should_compact(self, store):
        return store.len() > 5000
    def check(self, store):
        if self.should_compact(store):
            return store.compact()
        return None
```

Each compaction: N entries → 1 checkpoint. Call periodically. See [`CompactionPolicy`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/compaction.py) for the extension protocol.

> **Multi-peer safety:** only compact when all peers have synced. The policies don't know about peers — your application handles the safety check.

---

## Sync & Partial Replication

### Can I sync only part of the graph?

Two approaches, used together:

**1. GraphView (query-time filtering)** — see only your slice:

```python
from silk import GraphView

view = GraphView(store, node_types=["server"])
view.all_nodes()              # only servers
view.all_edges()              # only edges where BOTH endpoints are servers
view.get_node("svc-api")      # None — filtered out

# Also works with predicates
eu_view = GraphView(store, predicate=lambda n: n["properties"].get("region") == "eu")
```

**2. Filtered sync (bandwidth reduction)** — transfer fewer entries:

```python
offer = receiver.generate_sync_offer()
payload = sender.receive_filtered_sync_offer(offer, ["server"])
receiver.merge_sync_payload(payload)

# Combine with GraphView for clean queries
view = GraphView(receiver, node_types=["server"])
```

> **Honest limitation:** In a single-DAG oplog, causal closure may pull in entries of other types. Filtered sync is most effective for independent data types. For guaranteed isolation, use separate stores. True partial replication (fragmented DAGs) is tracked in a [research branch](https://github.com/Kieleth/silk-graph).

---

## Contributing

### How do I extend Silk?

Three extension points, all Python protocols:

| Extension | Protocol | Built-in | File |
|-----------|----------|----------|------|
| Query engines | [`QueryEngine`](QUERY_EXTENSIONS.md) | Fluent `Query` builder | `python/silk/query.py` |
| Compaction policies | [`CompactionPolicy`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/compaction.py) | `IntervalPolicy`, `ThresholdPolicy` | `python/silk/compaction.py` |
| Graph views | [`GraphView`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/views.py) | Type/subtype/predicate filters | `python/silk/views.py` |

For Rust-level contributions (new constraint types, new graph algorithms, sync optimizations): see [CONTRIBUTING.md](CONTRIBUTING.md).
