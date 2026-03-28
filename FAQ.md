# Silk FAQ

Answers to real questions from expert reviews and early users.

> **Quick links:** [README](README.md) · [WHY](WHY.md) · [DESIGN](DESIGN.md) · [PROOF](PROOF.md) · [PROTOCOL](PROTOCOL.md) · [SECURITY](SECURITY.md) · [ROADMAP](ROADMAP.md)

---

## Architecture & Scope

### How does Silk compare to NetworkX / TerminusDB / Neo4j?

Different tools for different jobs:

| | Silk | NetworkX | TerminusDB | Neo4j |
|---|---|---|---|---|
| **What it is** | Embedded CRDT graph library | In-memory graph library | Server-based versioned graph DB | Server-based graph DB |
| **Sync** | Automatic, conflict-free (CRDT) | None | Git-style (push/pull, manual conflict resolution) | Enterprise replication only |
| **Schema** | Write-time validation | None | OWL-based | Labels + indexes |
| **Offline** | Yes (fully local) | Yes (fully local) | No (requires server) | No (requires server) |
| **Graph algorithms** | BFS, DFS, shortest path, impact analysis, pattern match | 100+ (PageRank, centrality, Dijkstra, community detection) | WOQL/GraphQL queries | Cypher + GDS library |
| **Install** | `pip install silk-graph` | `pip install networkx` | Docker + pip | Docker or installer |

**Use Silk** when you need sync between peers, schema enforcement, and offline operation. **Use NetworkX** for graph analytics — on top of Silk's data if needed. **Use TerminusDB** when you need a server-side versioned graph with WOQL/GraphQL. **Use Neo4j** when you need Cypher, the GDS algorithm library, and enterprise infrastructure.

Silk + NetworkX is the intended pairing for applications that need both sync and analytics. See [BENCHMARKS.md](BENCHMARKS.md) for measured performance comparisons.

---

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

> **Performance note:** `pattern_match()` has O(n * b^d) complexity where n = nodes of the first type, b = average branching factor, d = sequence length. On dense graphs with high branching, this can be expensive. The `max_results` parameter bounds output size but not search cost. For complex pattern queries on large graphs, use a dedicated query engine via the `QueryEngine` extension protocol.

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

### Does Silk support class hierarchies or type inheritance?

Yes. Use `parent_type` on node types to declare is-a relationships:

```python
store = GraphStore("inst-1", {
    "node_types": {
        "thing": {"properties": {"name": {"value_type": "string", "required": True}}},
        "entity": {
            "parent_type": "thing",
            "properties": {"status": {"value_type": "string"}}
        },
        "server": {
            "parent_type": "entity",
            "properties": {"ip": {"value_type": "string"}}
        }
    },
    "edge_types": {
        "MONITORS": {
            "source_types": ["thing"],    # accepts thing, entity, server
            "target_types": ["entity"],   # accepts entity, server
            "properties": {}
        }
    }
})

store.add_node("s1", "server", "Web", {"name": "web-01", "ip": "10.0.0.1"})

# Hierarchy-aware queries: server shows up under "thing" and "entity"
store.query_nodes_by_type("thing")    # → includes s1
store.query_nodes_by_type("entity")   # → includes s1
store.query_nodes_by_type("server")   # → includes s1

# Hierarchy-aware edges: server is valid for source_types: ["thing"]
store.add_node("e1", "entity", "E", {"name": "target"})
store.add_edge("m1", "MONITORS", "s1", "e1")  # OK — server is-a thing
```

Three capabilities:
- **Property inheritance** — `server` inherits `name` (from thing) and `status` (from entity) automatically
- **Hierarchy-aware queries** — `query_nodes_by_type("entity")` returns entity AND server nodes
- **Hierarchy-aware edge validation** — `source_types: ["thing"]` accepts any descendant of thing

RDFS-level (rdfs9 + rdfs11). Fully CRDT-compatible — the hierarchy is monotonic, same as the rest of the ontology.

---

### Why no OWL-style reasoning (transitivity, inverse properties)?

Silk validates at write time. It does not infer new facts.

- **Structural** (Silk does this): `EMPLOYS` connects `organization→person` — validated
- **Semantic** (application does this): `EMPLOYS` is transitive — inference, not validation

If you need reasoning, run a reasoner (Pellet, HermiT) on top of Silk's graph data. Silk stays fast and predictable.

---

### Can open properties (D-026) cause type conflicts?

Yes. Open properties — properties not declared in the ontology — are accepted without type validation. If Peer A writes `update_property("node-1", "score", Int(42))` and Peer B writes `update_property("node-1", "score", String("high"))`, LWW resolves the conflict (later clock wins), but the surviving value might be of a type the application doesn't expect.

This is a deliberate trade-off (open-world assumption). The ontology enforces a typed core; unknown properties are the application's responsibility.

If you need type safety for a property after the fact, extend the ontology:
```python
store.extend_ontology({
    "node_type_updates": {
        "entity": {
            "add_properties": {
                "score": {"value_type": "int", "required": False}
            }
        }
    }
})
```

This validates `score` going forward. Existing values with the wrong type remain in the graph — they won't be retroactively validated unless you rebuild (e.g., via compaction + re-sync).

---

### How do I enforce graph-level invariants? ("Every server must have a RUNS_ON edge")

Silk validates **per-node** and **per-edge** at write time: types exist, properties match, constraints pass. It does NOT validate **cross-node** rules like "every server must have at least one RUNS_ON edge" or "if status is 'critical', there must be an assigned action."

This is deliberate. Graph-level invariants are **domain logic** — they belong in your application, not in the storage engine. The same way "every order must have a customer" belongs in application code, not in PostgreSQL triggers.

**Why Silk can't do this reliably:** During sync, the graph is transiently incomplete. Peer A adds a server. Peer B adds the RUNS_ON edge. Between syncs, A's graph has a server without a RUNS_ON edge — temporarily invalid. Rejecting the server would be wrong. The graph heals when B's entries arrive.

**The pattern: validate in your application after writes or syncs.**

```python
from silk import GraphStore

def validate_graph(store: GraphStore) -> list[str]:
    """Check graph-level invariants. Call after write batches or sync."""
    violations = []

    # Every server must have at least one RUNS_ON edge
    for server in store.query_nodes_by_type("server"):
        edges = store.outgoing_edges(server["node_id"])
        if not any(e["edge_type"] == "RUNS_ON" for e in edges):
            violations.append(f"server '{server['node_id']}' has no RUNS_ON edge")

    # Critical alerts must have an assigned action
    for alert in store.query_nodes_by_type("alert"):
        if alert["properties"].get("severity") == "critical":
            edges = store.outgoing_edges(alert["node_id"])
            if not any(e["edge_type"] == "ASSIGNED_TO" for e in edges):
                violations.append(f"critical alert '{alert['node_id']}' has no ASSIGNED_TO edge")

    # No decommissioned server should have active services
    for server in store.query_nodes_by_type("server"):
        if server["properties"].get("status") == "decommissioned":
            edges = store.outgoing_edges(server["node_id"])
            active_services = [e for e in edges if e["edge_type"] == "RUNS"]
            if active_services:
                violations.append(
                    f"decommissioned server '{server['node_id']}' still has "
                    f"{len(active_services)} active service(s)"
                )

    return violations

# Use it
violations = validate_graph(store)
if violations:
    for v in violations:
        print(f"WARNING: {v}")
```

**With subscriptions**, you can validate on every change:

```python
def on_change(event):
    if event["op"] in ("add_node", "add_edge", "remove_edge"):
        violations = validate_graph(store)
        if violations:
            alert_operator(violations)

store.subscribe(on_change)
```

**With time-travel**, you can validate historical states:

```python
snapshot = store.as_of(yesterday_ms)
violations = validate_graph(snapshot)  # same function works on snapshots
```

**Where this sits on the ontology spectrum:**

| Layer | What validates | Where it lives | Example |
|---|---|---|---|
| Property constraints | Single values | Silk (ontology) | `"port": {"min": 1, "max": 65535}` |
| Class hierarchy | Type relationships | Silk (parent_type) | server is-a entity |
| **Graph invariants** | **Cross-node rules** | **Your application** | **Every server must have RUNS_ON** |
| Semantic reasoning | Inferred facts | External reasoner | EMPLOYS is transitive |

Silk handles the first two layers. Your application handles the third. External tools (Pellet, HermiT) handle the fourth. Each layer belongs at the right level of abstraction.

> **Academic context:** This maps to the RDFS/SHACL distinction. RDFS (class hierarchy) is a schema-level concern — Silk handles it. SHACL (graph-level validation) is a data-quality concern — the application handles it. OWL (reasoning) is a knowledge-inference concern — external tools handle it. Silk's position: structural contracts in the engine, domain logic in the application.

---

### Why does Silk use last-writer-wins (LWW) for conflicts? Doesn't that lose data?

LWW is a deliberate trade-off. Per-property LWW means: two concurrent writes to different properties on the same node both survive; two concurrent writes to the *same* property — the one with the later HLC timestamp wins, the other is silently discarded.

That is fine for operational metadata (status fields, timestamps, config values). It is **not ideal** for semantically rich data where concurrent edits carry intent — tags, multi-valued fields, counters, or collaborative text. For those, richer merge strategies (OR-sets, counters, MV-registers) would preserve more information.

Silk chose LWW because it is the simplest convergent strategy that works for the graph-sync use case. Applications needing richer merge semantics should model them at the application layer — for example, storing a tag list as a node with individual tag nodes linked via edges (each edge is add-wins), rather than as a single property value.

> **Academic context:** LWW registers are well-understood in CRDT literature (Shapiro et al. 2011). They guarantee convergence at the cost of potential intent loss. This is a universal trade-off: richer merge algebras preserve more intent but add per-field type complexity. Silk keeps the merge layer simple and pushes semantic richness to the graph structure.

---

### When two peers write at the exact same time, who wins?

Silk's HLC total order is `(physical_ms, logical, instance_id)`. When physical time and logical counter are equal, the **lexicographically lower instance_id wins**. This is deterministic — both peers always agree on the winner — but it is not random. In a stable two-peer system, the same peer wins every tie.

In practice, true ties are rare. HLC physical time has millisecond resolution, and the logical counter increments on each operation. Two peers would need to write the same property in the same millisecond with the same logical counter. For most workloads, this doesn't happen.

If tie-breaking fairness matters for your use case, use instance IDs that don't create a predictable ordering (e.g., random UUIDs rather than `"peer-a"` / `"peer-b"`).

See [PROTOCOL.md](PROTOCOL.md) for the full clock ordering specification.

---

### Are edges validated during sync?

Yes. Every edge is validated against the ontology's source/target type constraints when it is materialized. During sync, entries are applied in topological (causal) order — nodes before edges — so both endpoints are always materialized before their edges are processed.

The code contains a defensive check: if an endpoint is not yet materialized at apply time, the edge is accepted without source/target validation. This guard exists for robustness against corrupted or malicious payloads, but cannot be triggered under normal operation because the Merkle-DAG's causal structure guarantees nodes precede their edges in topological order.

After any schema-changing sync (ExtendOntology, Checkpoint), a full graph rebuild runs, which re-validates all edges in topological order.

---

## Schema & Constraints

### What property constraints does Silk support?

Use the `constraints` field on property definitions. All constraints are enforced on both `add_node` and `update_property`.

| Constraint | Applies to | Example | SHACL equivalent |
|---|---|---|---|
| `enum` | string | `{"enum": ["active", "standby"]}` | `sh:in` |
| `min` | int, float | `{"min": 1}` | `sh:minInclusive` |
| `max` | int, float | `{"max": 65535}` | `sh:maxInclusive` |
| `min_exclusive` | int, float | `{"min_exclusive": 0}` | `sh:minExclusive` |
| `max_exclusive` | int, float | `{"max_exclusive": 100}` | `sh:maxExclusive` |
| `min_length` | string | `{"min_length": 1}` | `sh:minLength` |
| `max_length` | string | `{"max_length": 255}` | `sh:maxLength` |
| `pattern` | string | `{"pattern": "^[a-z0-9-]+$"}` | `sh:pattern` |

Unknown constraint names are silently ignored (forward compatibility for community-contributed validators).

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
                },
                "hostname": {
                    "value_type": "string",
                    "constraints": {
                        "pattern": "^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
                        "min_length": 1,
                        "max_length": 63
                    }
                },
                "cpu_percent": {
                    "value_type": "float",
                    "constraints": {"min_exclusive": 0.0, "max_exclusive": 100.0}
                }
            }
        }
    },
    "edge_types": {}
})

store.add_node("s1", "server", "Prod", {
    "status": "active", "port": 8080,
    "hostname": "web-01", "cpu_percent": 45.5
})  # OK

store.add_node("s2", "server", "Bad", {"status": "exploded"})     # ValueError: enum
store.add_node("s3", "server", "Bad", {"port": 0})                # ValueError: min
store.add_node("s4", "server", "Bad", {"hostname": "UPPER"})      # ValueError: pattern
store.add_node("s5", "server", "Bad", {"hostname": ""})            # ValueError: min_length
store.add_node("s6", "server", "Bad", {"cpu_percent": 0.0})       # ValueError: min_exclusive
```

The `pattern` constraint uses full regex via the Rust `regex` crate. Patterns are compiled at validation time. Invalid regex patterns produce a clear `ConstraintViolation` error.

---

### How do I add a custom constraint type?

Add a handler to `validate_constraints()` in [`src/ontology.rs`](https://github.com/Kieleth/silk-graph/blob/main/src/ontology.rs). Follow the existing pattern — check if the constraint key exists, match on the value type, return `ConstraintViolation` on failure.

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

> **Multi-peer safety:** `compact()` checks that all **registered** peers have synced before compacting. If any peer hasn't synced, it raises `RuntimeError`. Pass `safe=False` to override.
>
> **Limitation:** the safety check only knows about peers you've registered via `register_peer()`. Peers that exist but aren't registered (just came online, or were never registered) are invisible to the check. After compaction, unregistered peers that try to sync will need a full snapshot transfer instead of incremental sync. In a dynamic peer-to-peer system, this is a fundamental limitation — without consensus or a reliable membership protocol, you can't know all peers have converged. For static peer sets (known fleet), `verify_compaction_safe()` is reliable.

```python
# Check safety explicitly
safe, reasons = store.verify_compaction_safe()
if not safe:
    print(f"Unsafe: {reasons}")

# Default: raises if any peer hasn't synced
store.compact()           # safe=True (default)

# Force compaction (e.g., peer is permanently gone)
store.compact(safe=False)
```

Register peers via `store.register_peer(id, address)` and record syncs via `store.record_sync(id)`. If no peers are registered, compaction is always safe (single-node system).

---

### How much memory does Silk use?

Silk stores both the oplog (Merkle-DAG of all entries) and the materialized graph (nodes, edges, indexes) in memory. Measured on a realistic infrastructure workload (servers with 5 properties, services with 3 properties, RUNS_ON + DEPENDS_ON edges):

| Graph Size | Memory | Per Node | Snapshot |
|-----------|--------|----------|----------|
| 400 nodes / 750 edges | 0.8 MB | 2.0 KB | 0.2 MB |
| 4,000 nodes / 7,500 edges | 7.8 MB | 2.0 KB | 2.2 MB |
| 30,000 nodes / 50,000 edges | 55.8 MB | 1.95 KB | 15.4 MB |

Scaling is linear — ~2 KB per node (including edges, properties, per-property clocks, and adjacency indexes). Projected at 100K nodes: ~186 MB.

The oplog accounts for ~55% of memory (serialized entries + hash index), the materialized graph for ~45% (node/edge structs + adjacency indexes + type indexes).

Inspect memory at runtime:

```python
mem = store.memory_usage()
print(f"Oplog: {mem['oplog_bytes'] / 1024:.0f} KB")
print(f"Graph: {mem['graph_bytes'] / 1024:.0f} KB")
print(f"Total: {mem['total_bytes'] / 1024:.0f} KB")
```

There is no lazy loading, mmap, or eviction. The full graph lives in the process. For graphs under ~50K nodes, this is practical on any modern machine. For larger graphs, consider separate stores per domain or compaction to reduce oplog size.

> **Note:** `memory_usage()` returns approximate estimates. It does not account for heap allocations behind String/Vec values or allocator fragmentation. Actual memory may be 2-3x higher for string-heavy graphs. Use it for relative comparisons and order-of-magnitude planning, not precise capacity calculations.

> **Measured in [EXP-04](EXPERIMENTS.md).** Reproduce: `python experiments/test_memory_footprint.py`

---

### How does persistent storage (redb) handle writes?

Two modes:

**Immediate mode (default):** each write persists to disk immediately in a single atomic redb transaction. Safe — crash at any point, the write either committed or didn't. Slow for bulk writes (~1000x overhead vs in-memory) because each operation does a full fsync.

**Deferred mode:** writes go to memory immediately (read-your-writes), persist on explicit `flush()`. One fsync for N writes. ~276x faster than immediate for bulk writes, ~4x overhead vs pure in-memory.

```python
# Immediate (default — safe, slow for bulk)
store = GraphStore("id", ontology, path="graph.redb")
store.add_node(...)  # persisted immediately

# Deferred (fast bulk writes, explicit flush)
store = GraphStore("id", ontology, path="graph.redb")
store.set_flush_mode("deferred")
for server in servers:
    store.add_node(...)     # memory only — fast
store.flush()               # one fsync for all writes
```

**On crash in deferred mode:** entries since the last `flush()` are lost locally. If those entries were synced to a peer before the crash, the peer restores them on next sync. This is the local-first contract: writes are fast and available, durability is eventual.

Batch operations (`merge_sync_payload`) always batch into a single transaction regardless of flush mode.

On startup (`GraphStore.open(path)`), all entries are loaded from redb and the oplog is reconstructed via topological sort (O(n)). The materialized graph is rebuilt by replaying entries in causal order.

> **Measured in [EXP-08](EXPERIMENTS.md).** 500 entities: immediate = 2,237ms, deferred = 8.1ms (276x faster), in-memory = 2.0ms.

---

### What happens when a quarantined entry becomes valid?

If an entry is quarantined (e.g., uses an unknown node type) and later an `ExtendOntology` entry arrives that adds that type, the entry is un-quarantined automatically. The mechanism:

1. When sync merges an `ExtendOntology` entry, a full graph `rebuild()` runs
2. Rebuild clears the quarantine set and re-evaluates all entries against the evolved ontology
3. Entries that now pass validation are materialized into the graph
4. Subscribers are notified for any entry that was un-quarantined

This means quarantine is not permanent — it's re-evaluated whenever the ontology changes. The quarantine set is deterministic: two peers with identical oplogs produce identical quarantine sets after rebuild.

---

### Is the Value serialization format safe for type preservation?

Yes. Silk's `Value` enum uses `#[serde(untagged)]` for compact serialization. The potential concern: could `Int(1)` and `Float(1.0)` become ambiguous after serialization?

- **MessagePack** (used for entry hashing and sync): distinguishes integers from floats at the wire level. Safe.
- **JSON** (used by OperationBuffer for buffered ops): `serde_json` serializes `f64(1.0)` as `"1.0"` (always includes decimal point), so untagged deserialization correctly picks `Float` over `Int`. Safe.

This is verified by automated tests (4 Rust tests for JSON round-trip type preservation). No convergence risk from serialization ambiguity.

---

## Sync & Partial Replication

### What happens if sync messages are lost or corrupted?

Silk handles all common network failure modes:

| Condition | Behavior |
|-----------|----------|
| **Message lost** | Next sync round delivers the missing entries. Bloom filter detects what the peer still lacks. |
| **Duplicate delivery** | Idempotent — entries already in the oplog are skipped (0 new entries merged). |
| **Corrupted bytes** | BLAKE3 hash verification rejects tampered entries. Graph is unchanged. |
| **Truncated payload** | Deserialization fails. Graph is unchanged. |
| **50% random loss** | Converges within ~20 rounds of bidirectional sync (tested in [EXP-06](EXPERIMENTS.md)). |
| **Network partition** | Peers diverge independently. After reconnection, a single bidirectional sync converges all state. |

No special recovery mode is needed. The sync protocol is designed for unreliable delivery — each round is self-contained and makes progress toward convergence.

> **Tested in [EXP-06](EXPERIMENTS.md).** 8 fault injection scenarios including three-peer partitions, concurrent property conflicts, and rapid-fire writes interleaved with syncs.

---

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

### Can I compress sync payloads?

Yes. Compression is optional, applied at the transport boundary. Silk's sync methods produce and consume raw bytes — compression wraps those bytes.

```python
from silk.compression import ZlibCompression

comp = ZlibCompression()  # level=1 by default

# Sender
payload = store_a.receive_sync_offer(offer_bytes)
compressed = comp.compress(payload)       # 68% smaller
# send compressed over network

# Receiver
payload = comp.decompress(compressed)
store_b.merge_sync_payload(payload)
```

Built-in compressors:

| Compressor | Bandwidth | Latency overhead | When to use |
|-----------|-----------|-----------------|-------------|
| `NoCompression()` | 100% | 0% | LAN, local sync |
| `ZlibCompression(1)` | 32% | ~29% | WAN, metered connections |
| `ZlibCompression(6)` | 31% | ~59% | Bandwidth-constrained, CPU available |

Custom compressors implement the `SyncCompression` protocol:

```python
class LZ4Compression:
    def compress(self, data: bytes) -> bytes:
        return lz4.frame.compress(data)
    def decompress(self, data: bytes) -> bytes:
        return lz4.frame.decompress(data)
```

> **Measured in [EXP-05](EXPERIMENTS.md).** At 1000 entities, zlib-1 reduces payloads from 202 KB to 65 KB at a cost of 1.9ms. Higher zlib levels give <1% extra compression at 2-3x more CPU.

---

### How do I buffer operations before the store is open?

Use `OperationBuffer` — a filesystem-backed write-ahead buffer for graph operations. Operations are buffered as JSONL when the store isn't available (e.g., boot time), then drained into the store when it opens.

```python
from silk import OperationBuffer, GraphStore

# Pre-store: buffer operations (no store needed)
buffer = OperationBuffer("/var/lib/myapp/pending_ops.jsonl")
buffer.add_node("evt-1", "event", "Boot started", {"timestamp_ms": 1711526400000})
buffer.add_node("evt-2", "event", "Health check", {"timestamp_ms": 1711526401000})

# Later: store becomes available
store = GraphStore.open("/var/lib/myapp/graph.redb")

# Drain: apply all buffered ops through the normal store API
count = buffer.drain(store)  # → 2 ops applied
# Buffer is cleared after drain. Ontology validated. Subscriptions fire. HLC assigned.
```

**Key properties:**
- Buffer stores raw `GraphOp` payloads (no hash, no clock, no DAG parents — those are assigned at drain time)
- Ontology validation happens at drain, not at buffer time — invalid ops fail clearly
- D-023 subscriptions fire at drain time — EventBus sees buffered ops as normal events
- HLC timestamps reflect drain time, not event time — store real timestamps in properties for audit accuracy
- Buffer is local-only — no sync participation (buffered ops aren't entries until drained)
- `drain()` is explicit — the application controls when and what drains
- Buffer file is append-only JSONL, survives crashes, reopenable by new `OperationBuffer` instances

**Use cases:** Boot-time events (before Silk opens), pre-store initialization, offline operation queuing.

---

## Contributing

### How do I extend Silk?

Three extension points, all Python protocols:

| Extension | Protocol | Built-in | File |
|-----------|----------|----------|------|
| Query engines | [`QueryEngine`](QUERY_EXTENSIONS.md) | Fluent `Query` builder | `python/silk/query.py` |
| Compaction policies | [`CompactionPolicy`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/compaction.py) | `IntervalPolicy`, `ThresholdPolicy` | `python/silk/compaction.py` |
| Graph views | [`GraphView`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/views.py) | Type/subtype/predicate filters | `python/silk/views.py` |
| Sync compression | [`SyncCompression`](https://github.com/Kieleth/silk-graph/blob/main/python/silk/compression.py) | `ZlibCompression`, `NoCompression` | `python/silk/compression.py` |

For Rust-level contributions (new constraint types, new graph algorithms, sync optimizations): see [CONTRIBUTING.md](CONTRIBUTING.md).
