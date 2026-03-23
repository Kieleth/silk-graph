# Silk — Distributed Knowledge Graph Engine

A standalone Rust library (with Python bindings via PyO3) for distributed, peer-to-peer knowledge graphs. Zero external dependencies beyond the Rust toolchain. No PostgreSQL, no Redis, no Kafka, no external database of any kind. Each node in a cluster carries the complete graph, syncs peer-to-peer via Merkle-CRDT, and materializes views locally.

Silk is an independent library. It can be used by any application that needs a distributed, conflict-free, graph-structured data store.

**Repository**: https://github.com/Kieleth/silk-graph
**License**: TBD (open-source candidate)
**Whitepaper**: Planned — documenting the Merkle-CRDT graph store design, 5-primitive domain model, and the Wisdom loop for autonomous systems.

## Research Foundation

### Merkle-CRDTs (Sanjuán et al., 2020)

A Merkle-DAG can serve as both a logical clock and a transport layer for CRDTs. Each CRDT operation is stored as a content-addressed node in the DAG. Nodes link to their causal predecessors (the current "heads" at time of write). This gives:

- **Causal ordering** — edges encode "happened-before"
- **Deduplication** — content-addressed nodes are never duplicated (same op = same hash = already have it)
- **Integrity** — any tampering changes hashes, breaking the chain
- **Efficient sync** — "what do you have that I don't?" reduces to DAG traversal

Traditional CRDTs require at-least-once causal delivery — hard in practice. Merkle-CRDTs remove that requirement: if two replicas have the same set of DAG nodes, they compute the same state, regardless of delivery order.

**Reference implementations**: OrbitDB (JavaScript, on IPFS) and Automerge (Rust core, multi-language bindings). Both prove the theory works. OrbitDB's IPFS dependency is incidental — the Merkle-CRDT pattern works on any content-addressed store.

### Delta-State CRDTs

Three approaches to CRDT replication:

| Approach | What's shipped | Network requirement | Message size |
|----------|---------------|-------------------|-------------|
| Operation-based | Individual operations | Exactly-once, causal delivery | Smallest |
| State-based | Full state | Any delivery (tolerant) | Largest |
| Delta-state | Only the diff since last sync | Any delivery (tolerant) | Small |

Delta-state CRDTs are the practical choice: small messages like op-based, tolerant of message loss like state-based. Used by Ditto in production (Japan Airlines, US Air Force, Chick-fil-A).

For Silk: the Merkle-DAG is the transport for delta-state sync. Each sync exchanges only the operations the peer is missing — identified via Merkle tree anti-entropy.

### The Log (Jay Kreps / Kafka Design Principle)

Core insight: an append-only, totally-ordered sequence of records is the fundamental data structure for distributed systems. If every node processes the same log in the same order, they converge to the same state.

Making it distributed means replicating the log across nodes. With multiple writers (fleet instances), the log becomes a partial order (DAG), not a total order (single log). The Merkle-DAG captures this naturally.

### Anti-Entropy (Merkle Trees)

Used by Cassandra, Dynamo, Riak. Two nodes compare Merkle tree roots — if they differ, descend into subtrees to find differences. O(log n) to locate which records diverge.

Combined with Merkle-CRDTs: content-addressed operations + efficient diff detection + CRDT merge semantics. One mechanism for integrity, ordering, and sync.

### Bloom Filter Sync (from Automerge)

Automerge's sync protocol adds a practical optimization: instead of exchanging full Merkle trees, peers exchange bloom filters of their change hashes. False positives (~1%) are resolved in subsequent rounds. This reduces the initial sync handshake to a single round-trip in most cases.

Automerge's protocol:
1. Peer A sends: `{heads, bloom_filter_of_my_changes}`
2. Peer B computes: "changes I have that aren't in A's bloom filter" → sends them
3. If false positives caused missed changes, A explicitly requests them via `need` list
4. Termination: both sides generate no more messages

For new peers joining the fleet, Automerge sends the entire document as a compressed chunk rather than individual changes — faster for initial sync.

---

## Ontology-First Design

Silk is domain-agnostic. It has no built-in node types or edge types. Instead, every graph store begins with a **genesis entry** that defines an immutable **ontology** — the vocabulary and rules for that graph. The ontology must be defined before any data can be written, and it cannot be changed after genesis.

The ontology defines:
- **Node types** — with optional property schemas (name, type, required)
- **Edge types** — with strict source/target constraints (which node types can connect)
- **Property constraints** — type checking, required fields

This separation makes Silk usable in any domain: DevOps, biology, supply chain, social networks — each defines its own ontology. Silk enforces it.

```
Silk (engine)                    Your App (domain ontology)
┌──────────────────────┐        ┌──────────────────────────┐
│ Ontology enforcement │◄───────│ signal, entity, rule,    │
│ Merkle-DAG           │        │ plan, action             │
│ BLAKE3 hashing       │        │ OBSERVES, TRIGGERS,      │
│ Lamport clocks       │        │ RUNS_ON, PRODUCES...     │
│ CRDT sync            │        └──────────────────────────┘
│                      │
│ No domain knowledge  │        Any other domain
│                      │◄───────┌──────────────────────────┐
└──────────────────────┘        │ (define your ontology)   │
                                └──────────────────────────┘
```

The ontology is immutable by design. Like the rules in Conway's Game of Life — simple, fixed rules create complex emergent behavior. Changing the rules mid-game invalidates all prior state. If you need a different ontology, you need a different graph.

### Ontology Structure

```
Ontology
├── node_types: {name → NodeTypeDef}
│   └── NodeTypeDef
│       ├── description: Option<String>
│       └── properties: {name → PropertyDef}
│           └── PropertyDef
│               ├── value_type: string | int | float | bool | list | map | any
│               ├── required: bool
│               └── description: Option<String>
│
└── edge_types: {name → EdgeTypeDef}
    └── EdgeTypeDef
        ├── description: Option<String>
        ├── source_types: [String]     ← which node types can be source (strict)
        ├── target_types: [String]     ← which node types can be target (strict)
        └── properties: {name → PropertyDef}
```

The ontology itself is validated for internal consistency at creation time — all source/target types referenced in edge definitions must exist as node types. Invalid ontologies are rejected before any data can be written.

**For example, a domain model** with primitives, edge grammar, and DIKW/MAPE-K alignment could define its own ontology and pass it at graph creation.

---

## Technology

### Language: Rust + PyO3

Rust is the only viable language for an embeddable, memory-safe, concurrent data store callable from Python.

| Criterion | Why Rust wins |
|-----------|--------------|
| Memory safety | Compile-time guarantees. No UB. Critical for a data store. |
| Concurrency | `tokio` for async peer sync. `rayon` for parallel Merkle computation. GIL released during Rust work. |
| FFI overhead | Low (~50-200ns per call). Bulk ops via `bytes` are zero-copy. |
| Precedent | pydantic-core, polars, tiktoken, tokenizers, orjson — Rust engine + Python API is the established pattern. |
| Build | `maturin develop` — one command. |

**Eliminated alternatives**:
- C: No memory safety. No reason to choose it for a new project.
- Zig: Pre-1.0, no Python binding tooling, ecosystem too immature.
- Go: Cannot be sanely embedded (two-GC problem, cgo overhead, goroutine scheduler conflicts).
- C++: Same performance as Rust but with memory safety footguns and worse build tooling.

### Hashing: BLAKE3

| Hash | Speed | Cryptographic | Notes |
|------|-------|--------------|-------|
| **BLAKE3** | ~6-7 GB/s | Yes | Fastest cryptographic hash. Merkle tree internally. Used by IPFS, Solana, Cargo, Bazel. |
| SHA-256 | ~0.5-1 GB/s | Yes | Industry standard but 6-7x slower. |
| xxHash3 | ~50 GB/s | **No** | Not cryptographic. Collisions can be engineered. Unsuitable for content addressing. |

BLAKE3 provides cryptographic collision resistance at speeds that make content addressing essentially free.

### Serialization: MessagePack (rmp-serde)

| Format | Zero-copy | Schema required | Rust ecosystem |
|--------|-----------|----------------|---------------|
| **MessagePack** | No | No (schemaless) | `rmp-serde` — Serde integration, no codegen |
| Cap'n Proto | Yes | Yes (.capnp files) | Good, but requires schema files and codegen |
| FlatBuffers | Yes | Yes (.fbs files) | Good, same schema overhead |
| Protobuf | No | Yes (.proto files) | Good, same schema overhead |

For operations averaging <1KB, zero-copy deserialization saves single-digit microseconds — irrelevant compared to network/disk latency. MessagePack gives the simplest code path: define Rust types with `#[derive(Serialize, Deserialize)]`, done. No schema files, no code generation, no build step.

### Local Storage: redb

Embedded, transactional, zero-config. Written in pure Rust. Single-file database. ACID transactions. B-tree based. Replacement for LMDB/RocksDB without the C dependency.

### Networking: asyncio UDP + TCP

Peer-to-peer. UDP heartbeat gossip on port 7700 (fire-and-forget, 78 bytes, HMAC-SHA256). TCP Silk sync on port 7701 (length-prefixed frames, reliable, HMAC-SHA256). Inspired by Consul/Serf SWIM: UDP for gossip, TCP for state transfer. Each 5-second tick sends a heartbeat (UDP) and a sync exchange (TCP) to all peers. The sync protocol is Silk's existing `generate_sync_offer` → `receive_sync_offer` → `merge_sync_payload` cycle, transported over length-prefixed TCP frames.

---

## Architecture

### Entry Structure

Each graph operation is an entry in the Merkle-DAG:

```rust
struct Entry {
    hash: [u8; 32],           // BLAKE3(msgpack(self without hash))
    payload: GraphOp,         // the mutation (add_node, add_edge, etc.)
    next: Vec<[u8; 32]>,      // causal predecessors (heads at time of write)
    refs: Vec<[u8; 32]>,      // skip-list refs for fast traversal (configurable depth)
    clock: LamportClock,      // {instance_id, monotonic_counter}
    author: [u8; 32],         // instance public key
    sig: Vec<u8>,             // signature over (payload, next, refs, clock)
}

struct LamportClock {
    id: String,               // instance identifier
    time: u64,                // monotonic, incremented on each local op
}
```

**`next`**: Links to the current DAG heads at time of write. This encodes causality — if entry B has entry A in its `next`, B happened after A.

**`refs`**: Skip-list pointers into deeper history (default: 16 refs per entry). Accelerate DAG traversal from O(n) to O(log n) for long chains.

**`sig`**: Signature over the entry content (excluding hash and sig themselves). Allows any peer to verify the entry was created by a legitimate fleet instance.

### Graph Operations

```rust
enum GraphOp {
    // Genesis — must be the first entry in the DAG (next = []).
    // Defines the immutable ontology for this graph.
    DefineOntology {
        ontology: Ontology,
    },
    AddNode {
        node_id: String,
        node_type: String,          // validated against ontology
        label: String,
        properties: BTreeMap<String, Value>,  // validated against ontology
    },
    AddEdge {
        edge_id: String,
        edge_type: String,          // validated against ontology
        source_id: String,          // source node type checked
        target_id: String,          // target node type checked
        properties: BTreeMap<String, Value>,
    },
    UpdateProperty {
        entity_id: String,          // node or edge
        key: String,
        value: Value,
    },
    RemoveNode {
        node_id: String,            // tombstone — cascades to edges
    },
    RemoveEdge {
        edge_id: String,
    },
}
```

Note: `BTreeMap` (not `HashMap`) is used for properties to guarantee deterministic serialization order — required for content-addressed hashing.

### Conflict Resolution

| Operation | Conflict | Resolution |
|-----------|----------|------------|
| Concurrent `AddNode` (same ID) | Both want to create the same node | Add-wins. Properties merge (LWW per key). |
| Concurrent `AddNode` + `RemoveNode` (same ID) | Create vs delete | Add-wins (safer — prevents lost updates). |
| Concurrent `UpdateProperty` (same entity, same key) | Two values for one property | LWW — highest Lamport clock wins. Ties broken by instance ID (deterministic). |
| Concurrent `AddEdge` | Both add different edges | No conflict — edges union. |
| Concurrent `RemoveNode` + `AddEdge` (edge targets removed node) | Dangling edge | Edge is logically invalid — materialization skips edges with tombstoned endpoints. |

CRDT semantics guarantee convergence: after all entries propagate, every instance computes the same materialized graph, regardless of the order entries were received.

### Materialized Graph

The materialized graph is derived from the op log — like projections from events. It provides fast queries without replaying the full log.

```
Op Log (Merkle-DAG)                    Materialized Graph
┌──────────────────┐                   ┌──────────────────┐
│ AddNode(server-1) │ ──materialize──→ │ Nodes:           │
│ AddNode(api-svc)  │                  │   server-1 (entity)│
│ AddEdge(RUNS_ON)  │                  │   api-svc (entity) │
│ UpdateProp(cpu=85) │                  │ Edges:           │
│ AddNode(alert-1)  │                  │   api-svc RUNS_ON │
│ ...               │                  │     server-1      │
└──────────────────┘                   │ Indexes:         │
                                       │   by_type         │
                                       │   by_property     │
                                       │   adjacency_lists │
                                       └──────────────────┘
```

Materialization runs incrementally: each new entry updates the graph in-place. Full rematerialization (replay entire op log) is available for recovery.

### Graph Engine

Built-in graph algorithms, implemented in Rust for speed:

| Algorithm | Use case |
|-----------|----------|
| BFS / DFS | Traversal from a starting node |
| Shortest path (Dijkstra / BFS) | Find path between two entities |
| Subgraph extraction | Get all nodes/edges within N hops |
| Impact analysis | "What is affected if server-1 goes down?" — reverse dependency traversal |
| Pattern matching | Find all Signal → Rule → Plan → Action chains (the MAPE-K loop) |
| Topological sort | Dependency ordering for deploy sequences |

For small graphs (hundreds to low thousands of nodes), all of these run in microseconds in Rust. The Python API returns results as dicts/lists — no graph library dependency on the Python side.

### Sync Protocol

Sync piggybacks on the fleet coordination heartbeat (every 5 seconds):

```
Phase 1: Heartbeat exchange
  Peer A → Peer B: GET /fleet/heartbeat
  Response includes: {
    ...,
    silk_heads: ["<hash1>", "<hash2>"],    // current DAG heads
    silk_bloom: "<base64>",                 // bloom filter of recent ops
    silk_clock: 42                          // current Lamport time
  }

Phase 2: Delta detection
  A compares B's heads against its own:
  - If A has all of B's heads → A is up-to-date (or ahead)
  - If A is missing heads → compute missing entries

  A checks B's bloom filter:
  - Entries NOT in bloom → B doesn't have them → send to B
  - Entries IN bloom → B probably has them (1% false positive)

Phase 3: Delta exchange
  A → B: POST /fleet/sync {
    entries: [<serialized entries B is missing>]
  }
  B validates, merges, updates materialized graph.

Phase 4: Confirmation
  Next heartbeat round: heads should match.
  If not (bloom false positive), explicit request via `need` list.

Initial sync (new peer joining):
  Instead of entry-by-entry exchange, send compressed full graph snapshot.
  New peer materializes locally, then switches to delta sync.
```

**Consistency guarantee**: After one full heartbeat round (5 seconds) following a write, all reachable peers have the same graph state.

### Subscriptions

Silk provides in-process change notifications via callback subscriptions. Every graph mutation — whether from a local write or a remote merge — fires registered callbacks with a lightweight event dict describing what changed. This replaces PostgreSQL's `LISTEN/NOTIFY` and enables event-driven architectures without polling.

```python
# Subscribe — returns a subscription ID. Multiple subscribers allowed.
sub_id = store.subscribe(callback)

# Unsubscribe
store.unsubscribe(sub_id)

# Callback signature — called synchronously after each entry is applied
def callback(event: dict) -> None:
    """
    event = {
        "hash":       str,          # content-addressed entry hash (hex)
        "op":         str,          # "add_node" | "add_edge" | "update_property"
                                    # | "remove_node" | "remove_edge"
        "author":     str,          # instance ID of the writer
        "clock_time": int,          # Lamport time
        "local":      bool,         # True = local write, False = received via merge

        # Op-specific fields (from the Entry payload):
        "node_id":    str | None,   # add_node, remove_node
        "node_type":  str | None,   # add_node only
        "edge_id":    str | None,   # add_edge, remove_edge
        "edge_type":  str | None,   # add_edge only
        "source_id":  str | None,   # add_edge only
        "target_id":  str | None,   # add_edge only
        "entity_id":  str | None,   # update_property
        "key":        str | None,   # update_property
        "value":      Any | None,   # update_property
    }
    """
```

**Design properties** (see D-023 for rationale):

- **Per-entry granularity**: One callback invocation per entry applied. During a merge of 100 entries, the callback fires 100 times. Consumers batch in their own code if needed.
- **Local vs remote**: The `local` flag distinguishes writes originating from this store (`True`) from entries received via sync merge (`False`). Borrowed from Y.js's `origin` pattern. Critical for avoiding echo loops and routing differently.
- **Multiple subscribers**: Any number of callbacks can be registered. Each receives the same event. Consumers can implement fan-out, topic routing, and filtering in their application layer.
- **Error isolation**: If a callback raises an exception, Silk logs it and continues. Graph writes are never blocked by subscriber bugs.
- **No filtering**: Silk fires for every entry. Consumers filter in the callback — a single `if` statement. This keeps the Rust implementation simple and the API domain-agnostic.
- **No snapshot firing**: `GraphStore.from_snapshot()` creates a new store with no subscribers. After construction, the consumer subscribes and only sees new entries going forward.
- **Lightweight events**: The dict carries routing metadata (op type, entity IDs, author, clock), NOT full property maps. For `add_node`, `node_type` is included for routing but `properties` is not. Consumer calls `store.get_node()` for full state. Exception: `update_property` includes `key` and `value` — the change itself is essential for knowing what changed.

**Two ingress paths are hooked**:

1. `append()` — local writes. Fires after the entry is materialized and persisted, with `local=True`.
2. `merge_entries_vec()` — remote merges. Fires per new entry after materialization, with `local=False`.

The graph is fully updated before the callback fires. Subscribers can safely query the store for current state.

### Persistence

```
/var/lib/silk/
├── oplog.redb          # Merkle-DAG entries (content-addressed blocks)
├── heads.redb          # Current DAG heads (minimal recovery state)
├── graph.redb          # Materialized graph (nodes, edges, indexes)
└── meta.redb           # Instance ID, Lamport clock, peer state
```

All files are transactional (redb provides ACID). On crash recovery:
1. Read `heads.redb` — the current DAG heads
2. Verify `graph.redb` against heads — if consistent, resume
3. If inconsistent (crash during materialization), rematerialize from `oplog.redb`

The op log is the source of truth. Everything else is derived and recoverable.

---

## How Silk Replaces PostgreSQL

| Current (PostgreSQL) | Silk | Notes |
|---------------------|------|-------|
| `events` table (append-only) | Merkle-DAG op log | Same concept: append-only source of truth. Content-addressed instead of sequential. |
| 18 `*_view` projections | Materialized graph | Derived from op log. Incrementally updated. Same pattern, different engine. |
| `graph_nodes_view` + `graph_edges_view` | The graph IS the primary model | No separate KG projection — the graph is native. |
| `work_queue_view` (SKIP LOCKED) | Ops with claim semantics | Plan → Action transition with fencing tokens for singleton claims. |
| `LISTEN/NOTIFY` for SSE | Local subscription callbacks | `store.subscribe(callback)` — notified on every new op. |
| `metrics` table (PRIMARY) | Signal nodes in the graph | Metrics become Signal nodes with properties {name, value, timestamp}. Time-series queries via graph traversal with time-range filters. |
| `exceptions` table (PRIMARY) | Signal nodes in the graph | Exceptions become Signal nodes with properties {type, message, stacktrace}. |
| `alert_rules` table (PRIMARY) | Rule nodes in the graph | Alert rules become first-class Rule entities. Event-sourced via Silk ops. No more CRUD bypass. |
| `deploy_logs` table (PRIMARY) | Signal nodes linked to Action | Deploy log lines become Signals, edges link them to the deploy Action. No more direct psql writes from bash scripts. |
| `retention_settings` | Graph property on a config Entity | Configuration as data in the graph. |
| `alembic_version` | Not needed | No schema migrations — the graph ontology is immutable. New graphs get new ontologies. |

### What the migration fixes

Four current architectural gaps are eliminated:

1. **`metrics` / `exceptions`**: Currently bypass the event store (direct INSERT). In Silk, they're Signal nodes — fully event-sourced, syncable, queryable as graph.
2. **`alert_rules`**: Currently CRUD-managed, not event-sourced. In Silk, they're Rule nodes — created via ops, versioned, syncable.
3. **`deploy_logs`**: Currently written by a bash script via direct psql. In Silk, they're Signal nodes linked to Action nodes — no psql bypass.
4. **`coding_sessions_view` / `session_messages_view`**: Currently have direct writes outside the event flow (root console seed, stale prompt sweeper). In Silk, all writes go through ops.

---

## Python API

```python
import json
from silk import GraphStore

# Define the ontology — the vocabulary for this graph
ontology = json.dumps({
    "node_types": {
        "signal": {
            "description": "Something observed",
            "properties": {
                "severity": {"value_type": "string", "required": True},
            },
        },
        "entity": {
            "description": "Something that exists",
            "properties": {
                "ip": {"value_type": "string"},
                "status": {"value_type": "string"},
            },
        },
        "rule": {"properties": {}},
        "plan": {"properties": {}},
        "action": {"properties": {}},
    },
    "edge_types": {
        "OBSERVES": {
            "source_types": ["signal"], "target_types": ["entity"],
            "properties": {},
        },
        "RUNS_ON": {
            "source_types": ["entity"], "target_types": ["entity"],
            "properties": {},
        },
        "GUARDS": {
            "source_types": ["rule"], "target_types": ["entity"],
            "properties": {},
        },
    },
})

# Create a store — genesis entry with ontology is created automatically
store = GraphStore("instance-a", ontology)

# Graph mutations (each creates a Merkle-DAG entry, validated against ontology)
store.add_node("server-1", "entity", "Production Server", {"ip": "192.168.1.100", "status": "alive"})
store.add_node("api-svc", "entity", "API Service")
store.add_edge("e1", "RUNS_ON", "api-svc", "server-1")

# Ontology enforcement: invalid operations are rejected
store.add_node("x", "potato", "Bad")              # ValueError: unknown node type
store.add_node("s1", "signal", "Alert")            # ValueError: requires property 'severity'
store.add_edge("e2", "OBSERVES", "server-1", "api-svc")  # ValueError: cannot have source type 'entity'

# Introspection
store.node_type_names()  # ["action", "entity", "plan", "rule", "signal"]
store.edge_type_names()  # ["GUARDS", "OBSERVES", "RUNS_ON"]
store.ontology_json()    # full ontology as JSON string

# DAG structure
store.heads()            # current DAG head hashes
store.get(hash)          # entry by hash: {payload, next, clock_time, author, ...}
store.len()              # total entries including genesis

# Future (S-2+): Queries, sync, persistence
# nodes = store.query(node_type="entity", filters={"status": "alive"})
# path = store.shortest_path("api-svc", "server-1")
# delta = store.ops_since(last_known_hash)
# store.merge(remote_delta_bytes)
```

---

## Crate Structure

Silk is a standalone Rust crate. No imports from any consumer project. No shared types. Communication between Silk and any consumer happens through the public API only.

```
silk/
├── Cargo.toml                  # crate-type = ["cdylib", "rlib"]
├── pyproject.toml              # maturin build config
├── README.md                   # standalone documentation
├── LICENSE                     # open-source license (TBD)
├── deny.toml                   # cargo-deny config (license + vulnerability audit)
│
├── src/
│   ├── lib.rs                  # Rust library entry + public API surface
│   ├── ontology.rs             # Ontology, NodeTypeDef, EdgeTypeDef, validation
│   ├── entry.rs                # Entry struct, GraphOp, content addressing
│   ├── oplog.rs                # Merkle-DAG: append, traverse, heads
│   ├── graph.rs                # Materialized graph: nodes, edges, indexes
│   ├── engine.rs               # Graph algorithms: BFS, shortest path, impact analysis
│   ├── crdt.rs                 # Conflict resolution: LWW, add-wins, tombstones
│   ├── sync.rs                 # Sync protocol: delta detection, bloom filters
│   ├── store.rs                # Persistence layer (redb)
│   ├── clock.rs                # Lamport clock
│   ├── bloom.rs                # Bloom filter for sync negotiation
│   └── python.rs               # #[pymodule] + #[pyclass] wrappers (behind "python" feature)
│
├── python/
│   └── silk/
│       ├── __init__.py         # Re-exports from native module
│       └── __init__.pyi        # Type stubs for IDE support
│
├── tests/                      # Rust integration tests (cargo test)
│   ├── test_entry.rs           # ── Level 1: Unit-like ──
│   ├── test_clock.rs
│   ├── test_bloom.rs
│   ├── test_crdt.rs
│   ├── test_oplog.rs           # ── Level 2: Component ──
│   ├── test_graph.rs
│   ├── test_engine.rs
│   ├── test_store.rs
│   ├── test_sync.rs            # ── Level 3: Integration ──
│   ├── test_two_peers.rs
│   ├── test_partition_heal.rs
│   ├── test_snapshot_sync.rs
│   ├── test_concurrent_writers.rs
│   └── stress/                 # ── Level 4: Stress ──
│       ├── test_throughput.rs
│       ├── test_large_graph.rs
│       ├── test_many_peers.rs
│       └── test_chaos.rs
│
├── pytests/                    # Python tests (pytest)
│   ├── test_store_basic.py     # ── Level 1: Smoke ──
│   ├── test_graph_ops.py       # ── Level 2: Graph CRUD ──
│   ├── test_queries.py         # ── Level 3: Traversal + queries ──
│   ├── test_primitives.py      #    Signal/Entity/Rule/Plan/Action
│   ├── test_sync_python.py     # ── Level 4: Two stores syncing ──
│   ├── test_persistence.py     #    Crash recovery, rematerialization
│   └── test_stress.py          # ── Level 5: Python stress tests ──
│
├── docker/                     # Docker-based test environments
│   ├── Dockerfile.test         # Silk test image (Rust + Python + test harness)
│   ├── docker-compose.test.yml # Multi-node test scenarios
│   └── scenarios/
│       ├── two_node_sync.yml   # 2 peers, basic sync
│       ├── three_node_partition.yml  # 3 peers, network partition + heal
│       ├── rolling_update.yml  # N peers, rolling restart
│       ├── byzantine.yml       # Bad actor sending corrupt entries
│       └── stress.yml          # High-volume concurrent writes
│
├── benches/                    # Benchmarks (cargo bench / criterion)
│   ├── bench_entry.rs          # Entry creation + hashing throughput
│   ├── bench_oplog.rs          # Op log append + traverse
│   ├── bench_graph.rs          # Graph query latency
│   ├── bench_sync.rs           # Sync protocol throughput
│   └── bench_engine.rs         # Algorithm performance (BFS, shortest path)
│
└── docs/                       # Whitepaper + API docs
    ├── whitepaper.md           # Formal design document
    └── api.md                  # Public API reference
```

---

## Testing Strategy

TDD. Every feature is test-first. The test suite grows in complexity across five levels, from pure unit tests to multi-node chaos scenarios in Docker. Tests are the specification — if it's not tested, it doesn't exist.

### Test Pyramid

```
Level 5: Docker Compose scenarios       ┐
         Multi-node, network partitions, │ Slow (seconds-minutes)
         rolling updates, byzantine      │ Run: CI + manual
         ─────────────────────────────── │
Level 4: Stress tests                    │
         Throughput, large graphs,       │
         many concurrent writers         │
         ─────────────────────────────── │
Level 3: Integration tests               │ Medium (ms-seconds)
         Two-store sync, snapshot,       │ Run: cargo test + pytest
         partition and heal, persistence │
         ─────────────────────────────── │
Level 2: Component tests                 │
         Op log, graph, engine,          │ Fast (µs-ms)
         store, sync protocol           │ Run: cargo test
         ─────────────────────────────── │
Level 1: Unit tests                      │ Instant (µs)
         Entry, clock, bloom, CRDT,     │ Run: cargo test
         serialization, hashing         ┘
```

### Level 1: Unit Tests (Rust)

Pure functions, no I/O, no filesystem, no network. Every primitive data structure tested in isolation.

```
test_entry.rs:
  ✓ entry_hash_deterministic            same content → same BLAKE3 hash
  ✓ entry_hash_changes_on_mutation      different content → different hash
  ✓ entry_roundtrip_msgpack             serialize → deserialize = identical
  ✓ entry_signature_valid               signed entry verifies correctly
  ✓ entry_signature_reject_tampered     modified entry fails verification
  ✓ entry_next_links_causal             next[] points to valid predecessor hashes

test_clock.rs:
  ✓ lamport_monotonic                   clock always increases
  ✓ lamport_merge_takes_max             merge(local=5, remote=8) → 9
  ✓ lamport_tiebreak_deterministic      same time, different IDs → consistent order

test_bloom.rs:
  ✓ bloom_insert_and_check              inserted items are found
  ✓ bloom_false_positive_rate           rate < 2% with 10 bits/entry, 7 probes
  ✓ bloom_empty_contains_nothing        empty filter returns false for everything
  ✓ bloom_merge_union                   merged filter contains both sets
  ✓ bloom_serialization_roundtrip       serialize → deserialize = identical

test_crdt.rs:
  ✓ lww_latest_wins                     higher timestamp wins
  ✓ lww_tiebreak_by_author             same timestamp → lexicographic author ID
  ✓ add_wins_over_remove               concurrent add + remove → element exists
  ✓ tombstone_persists                  deleted node stays deleted after re-merge
  ✓ concurrent_property_updates         two writers, same key → deterministic winner
  ✓ merge_commutative                   merge(A,B) == merge(B,A)
  ✓ merge_associative                   merge(merge(A,B),C) == merge(A,merge(B,C))
  ✓ merge_idempotent                    merge(A,A) == A
```

### Level 2: Component Tests (Rust)

Each major component tested with in-memory or temp-dir storage. May use filesystem, no network.

```
test_oplog.rs:
  ✓ append_single_entry                 one entry, one head
  ✓ append_chain                        A → B → C, one head (C)
  ✓ append_fork                         A → B, A → C, two heads (B, C)
  ✓ append_merge                        fork then merge → one head
  ✓ heads_updated_on_append             heads reflect latest entries
  ✓ entries_since_returns_delta         only entries after given hash
  ✓ entries_since_empty_returns_all     no hash → entire log
  ✓ skip_refs_accelerate_traversal     refs reduce traversal steps
  ✓ topological_sort_respects_causality earlier ops before later ops
  ✓ duplicate_entry_ignored             same hash appended twice → no effect
  ✓ entry_not_found_error               requesting nonexistent hash → clean error

test_graph.rs:
  ✓ add_node_appears_in_query           add → query by type → found
  ✓ add_edge_creates_adjacency          edge → both endpoints know about it
  ✓ update_property_reflected           update → query → new value
  ✓ remove_node_cascades_edges          remove node → dangling edges removed
  ✓ remove_edge_preserves_nodes         remove edge → nodes unaffected
  ✓ query_by_type_filters               query(type="entity") → only entities
  ✓ query_by_property_filters           query(status="alive") → filtered results
  ✓ materialization_from_empty          replay op log → same graph as incremental
  ✓ incremental_equals_full             incremental updates match full rematerialization
  ✓ node_types_validated_against_ontology  only ontology-defined types accepted

test_engine.rs:
  ✓ bfs_traversal_from_node             visits all reachable nodes
  ✓ bfs_respects_depth_limit            depth=2 → only 2 hops
  ✓ bfs_filters_edge_types              only traverse DEPENDS_ON edges
  ✓ shortest_path_finds_path            A → B → C → D, shortest = 3
  ✓ shortest_path_no_path              disconnected nodes → None
  ✓ impact_analysis_reverse_traversal  reverse deps from node
  ✓ subgraph_extraction                 extract N-hop neighborhood
  ✓ pattern_match_mape_k_loop          find Signal→Rule→Plan→Action chains
  ✓ topological_sort_dependency_order   deploy order respects deps
  ✓ cycle_detection                     detects and reports graph cycles

test_store.rs:
  ✓ open_creates_files                  new store creates redb files
  ✓ open_existing_loads_state           reopen → same heads, same graph
  ✓ crash_recovery_from_oplog           corrupt graph.redb → rematerialize from oplog
  ✓ concurrent_readers_ok               multiple threads reading → no contention
  ✓ write_is_serialized                 concurrent writes → serialized correctly
```

### Level 3: Integration Tests (Rust + Python)

Multiple components working together. May use network (localhost). Tests the sync protocol end-to-end.

```
test_two_peers.rs:
  ✓ peer_a_writes_peer_b_receives      A writes → sync → B has the same graph
  ✓ both_write_merge                    A and B write concurrently → both converge
  ✓ sync_is_idempotent                  sync twice → same result
  ✓ sync_delta_only                     second sync sends only new entries
  ✓ bloom_filter_reduces_transfer       bloom prevents sending entries peer already has
  ✓ causal_order_preserved_after_sync  entries in correct order on both sides

test_partition_heal.rs:
  ✓ partition_both_write                 A and B write independently during partition
  ✓ heal_converges                       reconnect → both graphs identical
  ✓ heal_handles_conflicts              concurrent updates to same property → LWW
  ✓ heal_adds_win_over_removes          concurrent add + remove → add wins
  ✓ multiple_partitions_and_heals      partition → heal → partition → heal → converge

test_snapshot_sync.rs:
  ✓ new_peer_receives_snapshot          peer C joins → gets full snapshot from A
  ✓ snapshot_then_delta                  after snapshot, subsequent syncs are delta only
  ✓ snapshot_is_compressed               snapshot smaller than sum of entries
  ✓ snapshot_matches_incremental        snapshot materialization = incremental result

test_concurrent_writers.rs:
  ✓ ten_writers_one_store               10 threads writing to same store → consistent
  ✓ three_stores_round_robin_sync      A→B, B→C, C→A → all three identical
  ✓ write_during_sync                   writes during active sync → no corruption

Python integration tests (pytest):

test_store_basic.py:
  ✓ open_and_close                       store lifecycle
  ✓ add_node_query_node                  round-trip through Python API
  ✓ add_edge_query_edge                  edge creation and retrieval

test_graph_ops.py:
  ✓ crud_nodes                           create, read, update, delete nodes
  ✓ crud_edges                           create, read, update, delete edges
  ✓ property_types                       str, int, float, bool, list, dict

test_queries.py:
  ✓ traverse_bfs                         BFS from Python API
  ✓ shortest_path                        path finding from Python
  ✓ impact_analysis                      reverse dependency traversal
  ✓ pattern_match                        find primitive chains

test_primitives.py:
  ✓ signal_entity_rule_plan_action      each primitive type works
  ✓ edge_grammar_enforced               only valid edge types between primitives
  ✓ wisdom_loop_queryable               Action → Signal → Rule chain is traversable
  ✓ mape_k_full_cycle                   complete Monitor→Analyze→Plan→Execute cycle

test_sync_python.py:
  ✓ two_stores_sync_via_bytes           ops_since() + merge() round-trip

test_subscription.py:                                                    ✅ IMPLEMENTED
  ✓ subscription_fires_on_add_node      callback invoked on add_node
  ✓ subscription_fires_on_update        callback invoked on update_property
  ✓ subscription_fires_on_add_edge      callback invoked on add_edge
  ✓ subscription_fires_on_remove_node   callback invoked on remove_node
  ✓ subscription_fires_on_remove_edge   callback invoked on remove_edge
  ✓ event_fields_add_node               dict has op, node_id, node_type, author, clock_time
  ✓ event_fields_update_property        dict has entity_id, key, value
  ✓ event_fields_add_edge               dict has edge_id, edge_type, source_id, target_id
  ✓ event_local_true_for_local_write    local=True for direct writes
  ✓ event_local_false_for_merge         local=False for synced entries
  ✓ multiple_subscribers_all_fire       two subscribers both receive the event
  ✓ unsubscribe_stops_callbacks         after unsubscribe, no more events
  ✓ subscriber_error_does_not_block     exception in callback doesn't prevent write
  ✓ subscriber_receives_in_order        events arrive in append order

test_persistence.py:
  ✓ data_survives_restart               close → reopen → same data
  ✓ crash_recovery                       corrupt graph → auto-rematerialize
```

### Level 4: Stress Tests (Rust)

Performance and correctness under load. Run with `cargo test --release -- --ignored` (marked `#[ignore]` so they don't run in normal test suite).

```
stress/test_throughput.rs:
  ✓ append_ops_per_second               target: >100k ops/s on single core
  ✓ sync_entries_per_second             target: >50k entries/s over localhost TCP
  ✓ hash_throughput_blake3              BLAKE3 hashing rate (should be >1GB/s)
  ✓ serialization_roundtrip_rate        msgpack encode+decode rate

stress/test_large_graph.rs:
  ✓ 10k_nodes_1k_edges                  graph with 10,000 nodes
  ✓ 100k_nodes_10k_edges               graph with 100,000 nodes
  ✓ bfs_on_large_graph                  BFS performance at scale
  ✓ shortest_path_large_graph           pathfinding at scale
  ✓ impact_analysis_deep_deps           deep dependency chains (100+ levels)
  ✓ materialization_time_100k_ops       full rematerialization from 100k ops

stress/test_many_peers.rs:
  ✓ five_peers_full_mesh_sync           5 stores, all-to-all sync → convergence
  ✓ ten_peers_chain_sync                10 stores, chain topology → convergence
  ✓ peers_join_and_leave                dynamic membership, stores join/leave

stress/test_chaos.rs:
  ✓ random_ops_random_sync              random graph ops + random sync order → converge
  ✓ interleaved_writes_and_syncs        writes during syncs, syncs during writes
  ✓ rapid_fork_merge                    many concurrent forks in the DAG
  ✓ property_update_storm               1000 updates to same property → correct LWW
```

### Level 5: Docker Compose Scenarios

Real multi-node testing. Each scenario runs Silk instances in separate containers with network simulation (latency, partitions, packet loss). Uses `docker compose` for orchestration and `tc` (traffic control) for network shaping.

```
docker/Dockerfile.test:
  - Rust + Python + Silk built from source
  - tc / iptables for network simulation
  - Test harness binary (Rust) + pytest runner
  - Health check endpoint on each node

docker/scenarios/two_node_sync.yml:
  - 2 containers on a bridge network
  - Node A writes 1000 ops
  - Node B syncs from A
  - Verify: B's graph == A's graph
  - Measure: sync latency, bytes transferred

docker/scenarios/three_node_partition.yml:
  - 3 containers: A, B, C
  - Phase 1: all connected, all write
  - Phase 2: partition A from {B,C} (iptables DROP)
  - Phase 3: A writes, B and C write (diverged)
  - Phase 4: heal partition
  - Phase 5: full sync round
  - Verify: all three graphs identical
  - Verify: conflict resolution is correct (LWW, add-wins)

docker/scenarios/rolling_update.yml:
  - 3 containers, all synced
  - One by one: stop container, rebuild with new Silk version, restart
  - Verify: no data loss during rolling update
  - Verify: old and new versions can sync (backward compatibility)

docker/scenarios/byzantine.yml:
  - 3 containers: A, B, evil
  - evil sends entries with invalid signatures
  - evil sends entries with corrupted hashes
  - evil sends entries with impossible Lamport clocks
  - Verify: A and B reject all bad entries
  - Verify: A and B remain consistent

docker/scenarios/stress.yml:
  - 5 containers, full mesh
  - Each writes 10,000 ops concurrently (50k total)
  - Sync every 1 second
  - After all writes: one final sync round
  - Verify: all 5 graphs identical
  - Measure: total convergence time, memory usage, disk usage
```

### Running Tests

```bash
# Level 1-2: fast unit + component tests (seconds)
cargo test

# Level 3: integration tests including network (seconds)
cargo test -- --include-ignored integration

# Level 4: stress tests — release mode (minutes)
cargo test --release -- --ignored stress

# Python tests: all levels
pip install -e . && pytest pytests/

# Level 5: Docker scenarios (minutes)
docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit

# Specific scenario
docker compose -f docker/scenarios/three_node_partition.yml up --build --abort-on-container-exit

# Benchmarks (criterion)
cargo bench

# Full CI pipeline (everything)
cargo test && cargo test --release -- --ignored && pytest pytests/ && \
  docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit
```

### Test Properties

Every test asserts at least one of these properties:

| Property | What it means | Example test |
|----------|--------------|-------------|
| **Convergence** | All replicas with the same ops have the same graph | `heal_converges` |
| **Commutativity** | merge(A,B) == merge(B,A) | `merge_commutative` |
| **Associativity** | merge(merge(A,B),C) == merge(A,merge(B,C)) | `merge_associative` |
| **Idempotency** | merge(A,A) == A | `merge_idempotent` |
| **Causality** | Causal order preserved after sync | `causal_order_preserved_after_sync` |
| **Integrity** | Tampered entries rejected | `entry_signature_reject_tampered` |
| **Persistence** | Data survives restart | `data_survives_restart` |
| **Recovery** | Corrupt state is self-healing | `crash_recovery_from_oplog` |
| **Liveness** | System makes progress under load | `ten_writers_one_store` |

### CI Pipeline

```
On every push:
  1. cargo fmt --check
  2. cargo clippy -- -D warnings
  3. cargo test                            (L1-L3)
  4. maturin develop && pytest pytests/    (Python L1-L5)
  5. cargo deny check                      (license + vulnerability audit)

On merge to main:
  6. cargo test --release -- --ignored     (L4 stress)
  7. Docker scenario tests                 (L5)
  8. cargo bench → save results            (performance regression detection)

Release:
  9. maturin build --release               (wheels for Linux/macOS/Windows)
  10. cargo doc → publish                  (API docs)
```

---

## Implementation Phases

Dependencies and sequence. No time estimates. TDD: tests are written BEFORE implementation at every phase.

### Dependency Graph

```
S-0 (Scaffold) ─────────────────────┐
S-1 (Op Log) ──────────┐            │
                        ├──→ S-3 (Sync) ──→ S-5 (Integration)
S-2 (Graph + Engine) ──┘            │
                                    │
S-4 (Python API) ──────────────────┘
```

### Phase S-0: Scaffold ✅ COMPLETE

**Depends on**: Nothing

**Deliverables**:
- `silk/` crate with `Cargo.toml` (deps: blake3, rmp-serde, serde, serde_json, hex, pyo3)
- `pyproject.toml` with maturin config
- `clock.rs` — `LamportClock` (tick, merge, cmp_order)
- `entry.rs` — `Entry` struct with BLAKE3 content addressing, `GraphOp` enum with `DefineOntology`
- `ontology.rs` — `Ontology`, `NodeTypeDef`, `EdgeTypeDef`, `PropertyDef`, `ValueType`, validation
- `python.rs` — `PyGraphStore` with ontology-first creation (genesis entry), full validation
- `python/silk/__init__.py` + `__init__.pyi` — Python re-exports and type stubs
- `maturin develop` works, `from silk import GraphStore` works

**Tests** (35 Rust + 29 Python = 64 total):
- `clock.rs` tests: monotonic, merge-takes-max, merge-local-ahead, tiebreak deterministic, serialization roundtrip (6)
- `entry.rs` tests: hash determinism, mutation sensitivity, author/clock/next sensitivity, verify valid/tampered, msgpack roundtrip, causal links, all variants serialize, genesis entry, value roundtrip, hash hex (12)
- `ontology.rs` tests: validate node (valid, unknown type, missing required, wrong type, unknown property, optional absent, null accepted), validate edge (valid, unknown type, invalid source, invalid target), validate self (consistent, dangling source, dangling target), serialization roundtrip JSON + msgpack (15)
- `python.rs` tests: parse hex hash valid/wrong length/invalid chars (3)
- `pytests/test_store_basic.py`: genesis (7), node validation (7), edge validation (6), DAG structure (9) (29)

### Phase S-1: Op Log (Merkle-DAG)

**Depends on**: S-0

**Deliverables**:
- `oplog.rs` — append entries, traverse DAG, manage heads
- Skip-list refs for O(log n) traversal
- Persistence via redb (entries stored by hash)
- `heads()` — return current DAG heads
- `entries_since(hash)` — return all entries reachable from heads but not from given hash
- Causal ordering (topological sort of DAG)

**Tests written first**:
- `test_oplog.rs`: append single/chain/fork/merge, heads tracking, entries_since delta, skip refs, topological sort, duplicate ignored
- `test_store.rs`: open creates files, reopen loads state, crash recovery, concurrent readers
- `bench_oplog.rs`: append throughput (target: >100k ops/s)

### Phase S-2: Graph Materialization + Engine

**Depends on**: S-1

**Deliverables**:
- `graph.rs` — materialized graph: nodes, edges, adjacency lists, indexes
- `crdt.rs` / `graph.rs` — conflict resolution: per-property LWW (D-021), add-wins for topology (D-015), tombstones for deletes
- Incremental materialization: each new entry updates the graph
- Full rematerialization: replay entire op log (recovery)
- `engine.rs` — BFS, DFS, shortest path, subgraph extraction, impact analysis, pattern matching
- Persistence via redb (graph stored separately from op log)

**Tests written first**:
- `test_crdt.rs`: LWW, add-wins, tombstones, commutativity, associativity, idempotency
- `test_graph.rs`: CRUD nodes/edges, query by type/property, materialization from empty, incremental equals full, primitive type enforcement
- `test_engine.rs`: BFS, shortest path, impact analysis, subgraph extraction, pattern matching, cycle detection
- `pytests/test_graph_ops.py`: Python CRUD round-trips
- `pytests/test_queries.py`: Python traversal and queries
- `pytests/test_primitives.py`: 5 primitive types, edge grammar, wisdom loop queryable
- `stress/test_large_graph.rs`: 10k/100k nodes, algorithm performance
- `bench_graph.rs` + `bench_engine.rs`: query latency baselines

### Phase S-3: Sync Protocol ✅ COMPLETE

**Depends on**: S-1

**Deliverables**:
- `bloom.rs` — bloom filter: insert, check, merge union, serialization, enhanced double hashing (D-017), minimum 128 expected items (D-014)
- `sync.rs` — `SyncOffer` (heads + bloom), `SyncPayload` (entries + need list), `Snapshot` (full state), `entries_missing()`, `merge_entries()`
- Asymmetric offer/payload protocol (D-018): `generate_sync_offer` → `receive_sync_offer` → `merge_sync_payload`
- Snapshot generation and loading (`Snapshot::from_oplog`, `GraphStore.from_snapshot`)
- Add-wins fix: `last_add_clock` tracking on Node/Edge (D-015)
- Full Python API: `generate_sync_offer`, `receive_sync_offer`, `merge_sync_payload`, `merge_entries_bytes`, `snapshot`, `from_snapshot`
- Complete type stubs in `__init__.pyi` (graph queries, engine, sync)
- Tokio TCP sync endpoint deferred to S-7 (D-016)

**Tests** (23 Rust + 13 Python = 36 total, phase-specific):
- `bloom.rs` tests: insert/check, empty contains nothing, false positive rate <2%, merge union, serialization roundtrip, count tracking (6)
- `sync.rs` tests: offer from oplog, offer serialization, delta detection, nothing when in sync, need list for remote-only entries, bloom reduces transfer, merge basic/out-of-order/duplicates/invalid hash, snapshot roundtrip, snapshot bootstrap, full roundtrip A→B, full bidirectional, idempotent sync, **forces heads despite bloom FP (D-020), forces heads with ancestor closure (D-020)** (17)
- `pytests/test_sync.py` tests: offer/payload bytes types, A→B sync, bidirectional convergence, idempotent sync, sync with edges, graph queries after merge, snapshot roundtrip, snapshot then delta, snapshot preserves ontology, snapshot graph algorithms, LWW after sync, add-wins after sync (13)

**Not yet implemented** (deferred to later phases):
- `test_partition_heal.rs`: partition → diverge → heal → converge (Level 3, needs multi-store orchestration)
- `test_concurrent_writers.rs`: 10 threads, 3-store round-robin (Level 3)
- `stress/test_many_peers.rs`: 5-peer mesh, 10-peer chain (Level 4)
- `stress/test_chaos.rs`: random ops + random sync (Level 4)
- `bench_sync.rs`: sync throughput benchmarks
- Tokio TCP networking (D-016)

### Phase S-4: Python API

**Depends on**: S-0, S-2

**Deliverables**:
- `python.rs` — `#[pyclass] GraphStore` with all query/mutation methods
- Type stubs (`.pyi`) for IDE support
- `maturin develop` builds and installs in the local venv

**Tests written first**:
- `pytests/test_store_basic.py`: full smoke tests
- `pytests/test_persistence.py`: data survives restart, crash recovery
- `pytests/test_stress.py`: Python-side stress tests (throughput, concurrent access)

### Phase S-5: Docker Scenarios

**Depends on**: S-3, S-4

**Deliverables**:
- `docker/Dockerfile.test` — test image with Silk + test harness
- `docker/docker-compose.test.yml` — base compose for multi-node
- All scenario files (two_node_sync, three_node_partition, rolling_update, byzantine, stress)

**Tests written first** (scenario definitions):
- `two_node_sync.yml`: basic 2-node sync verification
- `three_node_partition.yml`: network partition + heal + convergence
- `rolling_update.yml`: backward-compatible rolling restart
- `byzantine.yml`: reject corrupt/invalid entries
- `stress.yml`: 5-node, 50k ops, convergence verification

### Phase S-6: Production Integration ✅ COMPLETE

**Depends on**: S-1, S-2, S-3, S-4

Bottom-up aggregate TDD with 5-level hierarchical testing.

**Deliverables**:
- Application ontology definition + `create_store()`
- Domain aggregates backed by Silk graph operations
- API route modules
- No ORM, no SQL — aggregates call `store.add_node()`, `store.query_nodes_by_type()`, etc.

**Tests**:
- L1 Store/Ontology
- L2 Aggregates
- L3 API Routes
- L4 Cross-Aggregate Integration
- L5 Cluster Sync (basic, aggregates, conflicts, snapshot bootstrap)

### Phase S-7: Fleet Integration

**Depends on**: S-6

**Deliverables**:
- Fleet coordinator uses Silk for state (replaces in-memory CRDT)
- Heartbeat protocol carries Silk sync data
- New instances bootstrap via Silk snapshot from a peer
- Seed file becomes the initial Silk state (ingested on first boot)

---

## Decisions Log

### D-001: 5 Primitives — Signal, Entity, Rule, Plan, Action (Example Ontology)
An example domain model, not a Silk engine feature. Any application defines its own ontology. This example uses 5 primitives for a DevOps domain: Signal (something observed), Entity (something that exists), Rule (a condition), Plan (a course of action), Action (something executed).

### D-002: Rust + PyO3
Rust core, Python API via PyO3/maturin. Same pattern as pydantic-core, polars, tiktoken. Memory safety, fearless concurrency, low FFI overhead.

### D-003: BLAKE3 for Content Addressing
6-7x faster than SHA-256, cryptographic, Merkle tree internally. No reason to use anything else for a new system.

### D-004: MessagePack for Serialization
Schemaless, compact, Serde integration. No codegen, no schema files. For sub-KB messages, zero-copy deserialization is irrelevant.

### D-005: Full PostgreSQL Replacement
Silk replaces PostgreSQL entirely. Events → op log. Projections → materialized graph. KG → native. Queue → graph transitions. SSE → subscriptions. Four current architectural gaps (metrics, exceptions, alert_rules, deploy_logs bypassing the event store) are eliminated.

### D-006: Action is Information, Not Knowledge
A domain modeling decision. Actions represent executed operations (Information tier in DIKW), not derived Knowledge.

### D-007: Wisdom is Enacted, Not Stored
A domain modeling decision. Wisdom emerges from the system's behavior (rule evaluation, plan selection), not from stored data.

### D-008: TDD — Tests Are the Specification
Every feature is test-first. Tests grow in five levels of complexity: unit → component → integration → stress → Docker scenarios. If it's not tested, it doesn't exist. The test suite IS the specification of correct behavior. Convergence, commutativity, associativity, idempotency, causality, integrity, persistence, recovery, and liveness are all mechanically verified.

### D-009: Silk is Standalone — Zero Consumer Dependencies
Silk imports nothing from any consumer project. No shared types, no shared config, no shared database. Silk is a general-purpose distributed knowledge graph engine. The boundary is the public API: `GraphStore.open()`, `add_node()`, `query()`, `ops_since()`, `merge()`. This separation enables independent versioning, independent testing, and independent publication.

### D-010: Open-Source Candidate
Silk is designed for open-source publication. The crate has its own README, LICENSE, and documentation. A whitepaper is planned documenting the Merkle-CRDT graph store design, the ontology-first approach, and the distributed sync protocol. With the ontology abstracted out (D-012), Silk is a general-purpose distributed graph engine usable in any domain — not tied to DevOps.

### D-012: Ontology-First — No Built-in Types
Silk has no built-in node types or edge types. The ontology is defined by the consumer and passed at graph creation as the immutable genesis entry (first entry in the DAG, `DefineOntology` op). Two design decisions:

1. **Ontology is immutable** — defined once at genesis, locked forever. No migration, no versioning. Changing the rules mid-game invalidates all prior state. Do your research before committing. This guarantees system integrity.
2. **Connection constraints are strict** — edge types enforce exactly which node types can be source/target. Like Conway's Game of Life: simple, fixed rules create complex emergent behavior. The rules define the space of possible interactions; complexity emerges from the data, not from evolving the rules.

Previous design (D-001) hardcoded `NodeType` as a Rust enum with 5 DevOps variants. This was removed. `node_type` is now a `String` validated against the ontology. Edge types were already strings. The `NodeType` enum was deleted; `ontology.rs` was added with `Ontology`, `NodeTypeDef`, `EdgeTypeDef`, `PropertyDef`, and `ValueType` structs plus full validation logic.

This separation enables Silk to be used in any domain: DevOps, biology, supply chain, social networks, knowledge management — each defines its own ontology. Silk enforces it.

### D-013: BTreeMap for Deterministic Serialization
Properties use `BTreeMap<String, Value>` instead of `HashMap<String, Value>`. HashMap iteration order is non-deterministic in Rust (randomized by default). Since entry hashes are computed from serialized content (`BLAKE3(msgpack(...))`), non-deterministic serialization order would produce different hashes for identical content. BTreeMap guarantees sorted key order, making content addressing deterministic. This was caught by the `entry_hash_deterministic` test.

### D-011: Docker Compose for Complex Scenario Testing
Multi-node scenarios (partitions, rolling updates, byzantine faults, stress) are tested in Docker Compose environments with network simulation via `tc`/`iptables`. These are not unit tests — they are full system tests that verify distributed properties hold under real network conditions.

### D-014: Bloom Filter Minimum Size — 128 Expected Items
Bloom filters sized for very small sets (e.g., 2-3 entries) produce bit arrays so small (64 bits minimum) that false positive rates far exceed the configured 1%. Discovered during S-3 implementation: a bloom filter built from 2 entries produced a false positive for a third entry, causing `entries_missing` to skip a needed entry and the bidirectional sync test to fail silently (peer received 0 entries instead of 1).

Fix: `SyncOffer::from_oplog` uses `expected_items = max(actual_count, 128)`. This guarantees the bloom filter has enough bits for meaningful probabilistic filtering regardless of how few entries exist. The 128 minimum gives ~1228 bits with k=7 hashes — sufficient headroom that false positives for a handful of items are vanishingly unlikely. The cost is negligible (~160 bytes per sync offer even for tiny stores).

**Root cause**: The optimal bloom filter formula `m = -n * ln(p) / ln(2)^2` gives ~19 bits for n=2, p=0.01. With 64-bit minimum and k=5 hashes, 2 inserted items set ~10 bits, leaving only ~54 unset — enough that a random 32-byte BLAKE3 hash has a non-trivial chance of mapping all k probes to set bits.

### D-015: Add-Wins via `last_add_clock` Tracking
Standard LWW (Last-Writer-Wins) for the tombstone flag is insufficient for correct add-wins semantics. If a remove has a higher Lamport clock than a concurrent re-add, the remove wins under LWW — violating the CRDT guarantee that concurrent add + remove should resolve to "exists."

Fix: `Node` and `Edge` structs carry a `last_add_clock` field (separate from `last_clock`). Updated only by `apply_add_node` / `apply_add_edge`. `apply_remove_node` / `apply_remove_edge` only set `tombstoned = true` if `clock_wins(remove_clock, last_add_clock)` — i.e., the remove must be strictly newer than the most recent add. If the re-add and remove are concurrent (same Lamport time, different instance IDs), the remove cannot win because:

- `clock_wins((remove_id, T), (add_id, T))` requires `remove_id > add_id` lexicographically
- But `apply_add_node` always sets `tombstoned = false` unconditionally, so if the add is applied after the remove in replay order, the node resurrects
- And if the remove is applied after the add, the `last_add_clock` check prevents the tombstone

This gives order-independent convergence: regardless of which entry is materialized first, the final state is the same. Verified by `add_wins_over_remove` (Rust) and `test_add_wins_after_sync` (Python, bidirectional sync with 2 stores).

**Prior behavior**: `apply_remove_node` checked `clock_wins(remove_clock, last_clock)`, where `last_clock` is updated by ANY operation (add, update, remove). This meant a property update could advance `last_clock` past a concurrent re-add, allowing a remove to tombstone a node that should have survived.

### D-016: Sync Protocol is Transport-Agnostic
The sync protocol (bloom filter negotiation, delta detection, merge, snapshot) is implemented as pure functions over in-memory data structures. No networking code, no async, no tokio. The protocol produces and consumes `Vec<u8>` (MessagePack-serialized messages) that any transport can carry.

This separation means:
1. **Testing is fast and deterministic** — no sockets, no timers, no flaky network tests at the unit/component level
2. **Transport is pluggable** — tokio TCP for fleet heartbeats, HTTP for API-mediated sync, even file-based transfer for air-gapped environments
3. **The hard part is done** — bloom filter sizing, delta computation, merge ordering, conflict resolution, snapshot bootstrap — all verified with 15 Rust + 13 Python tests

Tokio TCP transport (the `S-3` networking deliverable from the original spec) is deferred to when fleet integration (S-7) needs it. The primitives are ready; the wire protocol is just plumbing.

### D-017: Enhanced Double Hashing for Bloom Filter Probes
Bloom filter bit indices are computed using enhanced double hashing: `h_i = h1 + i*h2 + i^2 (mod m)`, where `h1` and `h2` are derived from the first 16 bytes of the 32-byte BLAKE3 hash (8 bytes each, interpreted as little-endian u64). The quadratic term `i^2` breaks up clustering that plain double hashing can exhibit.

This avoids the need for k independent hash functions. Since the input is already a cryptographic hash (BLAKE3), the bits are uniformly distributed — splitting them into segments gives independent-enough values for bloom filter probing. Same technique used by Guava's `BloomFilter` implementation.

### D-018: Sync Offer/Payload Asymmetry
The sync protocol is intentionally asymmetric: `receive_sync_offer(remote_offer)` computes what the *remote* peer is missing from *our* store. It does NOT compute what we need from them. This means a full bidirectional sync requires two exchanges:

```
# A sends entries to B:
offer_b = B.generate_sync_offer()           # B advertises its state
payload_for_b = A.receive_sync_offer(offer_b)  # A computes what B lacks
B.merge_sync_payload(payload_for_b)          # B merges A's entries

# B sends entries to A:
offer_a = A.generate_sync_offer()           # A advertises its state
payload_for_a = B.receive_sync_offer(offer_a)  # B computes what A lacks
A.merge_sync_payload(payload_for_a)          # A merges B's entries
```

This matches the Automerge sync protocol design: each message is a response to the peer's state advertisement. The `SyncPayload` also carries a `need` list (hashes the sender wants from the peer) for resolving bloom filter false positives in subsequent rounds.

### D-019: Ancestor Closure in `entries_missing` — Causal Chain Integrity

Bloom filter false positives don't just waste bandwidth — they can break causal chains. If a parent entry gets a false positive (bloom says "peer has it" but they don't), the child entry is included in the sync payload but the parent is excluded. The receiving peer cannot merge the child because its parent hash doesn't resolve. This causes `merge_entries` to fail with "unresolvable parents."

Discovered by the `chaos_random_ops_random_sync` stress test (Level 4): 4 peers, 200 random operations, random partial syncs. With 200+ entries across 4 peers, the bloom filter's ~1% false positive rate reliably hits at least a few parent entries, breaking 21 causal chains in the test case.

Fix: `entries_missing` now performs a **transitive ancestor closure** after the initial bloom filter pass. For every entry selected for sending, all parent hashes are checked: if a parent is not already in the send set and not in the remote's head set (which they definitely have), it's added to the send set. This repeats until no new ancestors are discovered. The result: the sync payload is always causally complete — every entry's parents are either already at the remote peer or included in the payload.

Cost: the closure loop is O(E * D) where E is entries being sent and D is max DAG depth. For typical workloads (hundreds of entries, shallow DAGs), this is negligible. The alternative — multi-round sync to resolve false positives via `need` lists — adds network latency and protocol complexity. Single-round causal completeness is simpler and faster.

**Prior behavior**: `entries_missing` filtered by `!bloom.contains(hash)` and sent exactly that set. This was correct only when the bloom had zero false positives — an impossibility by design.

### D-020: Head-Forcing in `entries_missing` — Bloom FP on DAG Tips

Bloom filter false positives on **head entries** (DAG tips) are unrecoverable by the Phase 2 ancestor closure. The ancestor closure walks parents of entries already in the send set — but a head entry has no descendants in the send set to trigger the walk. If the bloom falsely reports that the remote has our head, the entry is permanently missed. Multiple sync rounds cannot fix it because the bloom is deterministic — the same FP recurs every time.

This was originally documented as D-027 in session notes. It manifested specifically with a full ontology (14 node types, 9 edge types) where the larger entry set created hash collisions in the bloom filter that consistently hit head entries.

**Fix**: Added Phase 1.5 between bloom check (Phase 1) and ancestor closure (Phase 2). Phase 1.5 forces all our heads into the send set when they are not in the remote's heads set. Rationale: if our head is not one of the remote's heads, the remote either doesn't have it or has moved past it. In either case, sending it is safe — `merge_entries` is idempotent and silently ignores duplicates.

Phase 2 then pulls in the full causal chain from the forced heads backward to the nearest shared ancestor, ensuring the payload is causally complete.

**Trade-off**: When the remote is strictly ahead of us (our head is an ancestor of theirs, not a head), Phase 1.5 may send entries the remote already has. This wastes bandwidth but never causes incorrectness. The alternative — trusting the bloom for head entries — leads to permanent data loss, which is unacceptable.

**Prior behavior**: `entries_missing` relied solely on the bloom filter for Phase 1 and ancestor closure for Phase 2. Head entries that were bloom FP'd had no recovery mechanism.

### D-021: Per-Property LWW — Concurrent Non-Conflicting Updates Must Both Win

The original `apply_update_property` used **node-level LWW**: it compared the incoming clock against `node.last_clock` (the clock of the last operation that touched any property on the node). This meant concurrent updates to *different* properties on the same node could conflict: if inst-B's update to key Y set `last_clock = {3, node-b}`, inst-A's update to key X with `{3, node-a}` would be rejected because `"node-a" < "node-b"` lexicographically.

This is incorrect for a multi-register CRDT. Two writes to different keys are non-conflicting — both must be accepted regardless of application order.

**Fix**: Added `property_clocks: HashMap<String, LamportClock>` to both `Node` and `Edge`. Each property key tracks the clock of its last write independently. `apply_update_property` compares against `property_clocks[key]`, not `node.last_clock`. `apply_add_node` and `apply_add_edge` initialize per-property clocks from the add operation's clock. The entity-level `last_clock` is still updated for add-wins tracking but is no longer used for property-level LWW.

**Verification**: Two Rust tests exercise both application orders (inst-A first, inst-B first) for concurrent updates to different properties. Both orders produce identical results — the hallmark of a convergent CRDT.

**Prior behavior**: Whichever concurrent update was applied second would either win (if its clock was higher) or lose (if its clock was lower), regardless of whether it touched the same property.

### D-022: Interleaved Entry Materialization After Merge

`merge_entries_vec` (the Python binding's merge path) identified new entries by calling `entries_since(None)` and then `skip(len_before)` — assuming new entries would appear at the end of the topological sort. This assumption breaks when merging entries from a **concurrent branch**: the topo sort interleaves entries by Lamport time, and a merged entry can land anywhere in the sorted sequence, not necessarily at the tail.

**Symptom**: An entry was correctly received and inserted into the oplog (verified by oplog length and head tracking), but never applied to the materialized graph because `skip(len_before)` jumped past it.

**Fix**: Before merge, collect the hash set of all existing entries. After merge, iterate the full topo-sorted entry list and apply only entries whose hash is not in the original set. This correctly identifies new entries regardless of their position in the sort order, and applies them in proper topological order for correct LWW resolution.

**Cost**: One `entries_since(None)` call before merge to build the hash set. For typical store sizes (hundreds to low thousands of entries), this is negligible. The alternative — tracking inserted hashes inside the merge function — would require plumbing changes across the Rust-Python FFI boundary.

### D-023: Subscription API — Entry-Level, Multi-Subscriber, Error-Isolated

Silk provides in-process change notification via `store.subscribe(callback)`. Design modeled after OrbitDB (Merkle-CRDT with oplog entry events) with Y.js's origin tracking (local vs remote flag).

**Key decisions**:

1. **Per-entry, not per-batch**: Each entry applied fires one callback invocation. During merge of N entries, N callbacks fire. Batching is the consumer's concern. Rationale: the Entry is Silk's atomic unit of change. Sub-entry granularity doesn't exist; super-entry batching is application-specific.

2. **Multiple subscribers**: `Vec<(u64, PyObject)>` in Rust, monotonic ID counter. Unlike SQLite (single hook, replaced on set), Silk is a library used by applications with multiple independent subsystems. Each subscriber is independent.

3. **`local` flag**: `True` for entries created by `append()` (this store wrote it), `False` for entries received via `merge_entries_vec()` (remote sync). Borrowed from Y.js's `origin` convention. Essential for consumers to distinguish "I did this" from "someone else did this" — prevents echo loops, enables differential processing.

4. **Error isolation**: Subscriber exceptions are logged and swallowed. The graph write succeeds regardless. Rationale: the op log is the source of truth; subscriptions are side effects. A subscriber bug must not compromise data integrity. Same philosophy as RocksDB's EventListener.

5. **No server-side filtering**: Silk fires for every entry, every subscriber. Consumer filters with `if event["op"] == ...` or `if event["node_type"] in ...`. Adding filter predicates to the Rust subscription registry (like Neo4j CDC selectors) would make the API domain-aware — violating D-009 (standalone) and D-012 (no built-in types). A Python `if` statement costs ~50ns; the simplicity is worth it.

6. **Lightweight event dict**: Carries Entry payload metadata (op type, entity IDs, author, clock), not full property maps. `add_node` includes `node_type` for routing but omits `properties`. `update_property` includes `key` and `value` — the change itself. Consumer queries the store for full state when needed. This keeps callback overhead minimal.

7. **No snapshot firing**: `from_snapshot()` creates a new store with no subscribers. Historical entries from the snapshot do not fire callbacks. Subscribers only see entries applied after they register. This avoids the "bootstrap flood" problem (OrbitDB fires `update` for every replicated entry, which can overwhelm consumers on initial sync).

**Implementation notes**:

- `PyGraphStore` holds `subscribers: Vec<(u64, PyObject)>` + `next_sub_id: u64`. Monotonic counter, no reuse.
- `subscribe()` and `unsubscribe()` are `#[pymethods]` (Python-facing). `notify_subscribers()` and `entry_to_event_dict()` are private Rust methods.
- `append()` clones the Entry before moving it into the backend, so the notification can reference it after persistence. The clone cost is negligible (~1KB per entry).
- `merge_entries_vec()` notifies per new entry inside the existing loop that applies entries to the materialized graph. No separate iteration.
- `notify_subscribers()` acquires the GIL via `Python::with_gil()`. This is safe because all call sites are already in Python-called methods (GIL is held). The explicit `with_gil` ensures the callback has a valid Python context.
- Error isolation uses `eprintln!` for logging. A structured logging mechanism (e.g., `tracing` crate) can replace this later without API changes.

**Research basis**: SQLite WAL/update hooks (single subscriber, minimal payload), RocksDB EventListener (multiple subscribers, typed events, error isolation), Y.js update events (origin tracking, transaction batching), Automerge patch callbacks (fine-grained path+action), OrbitDB events (Entry-level, same event for local and remote), Neo4j CDC (rich filtering, pull-based).

### D-024: Subtypes — Per-Subtype Property Definitions within Coarse Types

`NodeTypeDef` gains an optional `subtypes` map. Each subtype has its own property definitions. When a node type defines subtypes, `add_node` requires a `subtype` parameter and validates properties against the subtype's definition (merged with any type-level common properties). Edge constraints reference top-level types only, not subtypes.

**Rationale**: Enables coarse-type ontologies (e.g., 5 broad node types) with fine-grained per-subtype property enforcement. Without subtypes, coarse types force a union-bag of all possible properties (losing CWA enforcement) or require application-layer validation (splitting enforcement across layers).

**Backward compatible**: Types without subtypes work exactly as before. The `subtype` field on `GraphOp::AddNode` is `Option<String>` with `#[serde(default)]` — old serialized entries deserialize with `subtype: None`. Edge validation is unchanged — it uses `node_type`, not subtype.

Subtypes are a generic Silk feature. Any ontology consumer can use them. Silk remains standalone (D-009).

**Research basis**: Google KG (~1,500 types for 5B+ entities), Wikidata (`instance_of` P31 — type hierarchy in data, not schema), BFO (ISO/IEC 21838-2 — 34 categories, domain ontologies extend via downward population), Neo4j (coarse types with properties), ontological parsimony (Occam's Razor). See [architecture.md](architecture.md) for full research.

### D-025: ObservationLog — The Log/KG Duality

Silk gains a second store type: `ObservationLog`. While `GraphStore` embodies the "table" (decisions, CRDT-synced, permanent), `ObservationLog` embodies the "log" (raw observations, local-only, TTL-pruned). Two redb files, two purposes, one crate.

**The problem**: A knowledge graph stores decisions, not data. But the detection layer needs raw observations (health check results, CPU metrics, container status) to evaluate Rules and produce Signals. Without a local observation store, the detection layer can only operate in-memory — losing all history on restart and preventing windowed evaluation ("3 failures in 10 minutes").

**Why not use GraphStore?**: Three reasons from production experience:
1. **C-099 (store bloat)**: An 11MB GraphStore caused 100% CPU on boot. The Merkle-DAG oplog grows monotonically — entries can't be deleted without breaking hash chains. Observations at 60s cadence would bloat the oplog by ~10M entries/day at scale.
2. **CRDT sync overhead**: Every GraphStore entry syncs to all fleet peers. Raw observations are local — "server-7 CPU was 47%" doesn't need to be on server-3.
3. **SA-001 (DIKW filter)**: The KG stores Knowledge and Wisdom. Raw observations are Data. Mixing them violates the fundamental design principle.

**Why not an external system (Kafka, NATS, SQLite)?**: Zero external dependencies for core operations. The observation layer must survive everything the KG survives — Docker crashes, network partitions, disk pressure. Adding a Go binary (NATS) or JVM (Kafka) creates unnecessary dependencies. SQLite via Python stdlib would work but splits persistence across two engines (redb + SQLite).

**The design**: A redb-backed append-only log with TTL truncation. No Merkle-DAG (no hash chains = deletable entries). No CRDT sync (local-only). No ontology validation (raw key-value, not typed nodes). Separate file from GraphStore.

```
ObservationLog (observations.redb)
├── Table: observations
│   Key:   (source: &str, timestamp_ms: u64)  — compound key
│   Value: msgpack { value: f64, metadata: BTreeMap<String, String> }
│
├── append(source, value, metadata)     — O(1) write
├── query(source, since_ts)             — O(log n) range scan
├── query_latest(source)                — O(log n) reverse scan
├── truncate(before_ts)                 — bulk delete, O(n_deleted)
├── sources()                           — distinct source prefixes
└── size_bytes() / count()              — monitoring
```

**Scale target**: 100 servers, 1200 projects, ~10M observations/day, ~1GB/day with 24h retention. redb handles this comfortably (B-tree, ACID, single-file).

**Hierarchical federation**: Observations never leave the fleet. Only Signals (derived from observations by the detection layer) flow UP to parent instances. This is the DIKW filter applied to the network topology.

**Kreps' duality in Silk terms**: "If you have a log of changes, you can apply these changes to create a table." The ObservationLog is the raw change stream. The GraphStore's Signals are the materialized "table" of significant events. The detection layer is the stream processor.

Any Silk consumer could use ObservationLog for time-series data, audit trails, or sensor readings. Silk remains standalone (D-009).

---

## D-026: Open Properties

**Decision**: The ontology defines the minimum schema, not the maximum. Unknown properties are accepted without type validation. Unknown subtypes are accepted with type-level validation only.

**Problem**: Silk originally rejected any property not declared in the ontology (`ValidationError::UnknownProperty`) and any subtype not listed in the node type definition (`ValidationError::UnknownSubtype`). This meant every new domain concept required an ontology change → new genesis entry → store recreation → data loss. The ontology was a ceiling, not a floor.

**Solution**: Three changes in `validate_properties()` and `validate_node()`:

1. **Unknown properties**: `continue` instead of `Err(UnknownProperty)`. Properties not in the ontology are stored as-is without type validation.
2. **Unknown subtypes**: When a subtype isn't in the `subtypes` map, validate against type-level properties only (skip subtype-specific validation).
3. **Subtypes on types without subtype declarations**: Accept them. Validate type-level properties only.

**What stays enforced**:
- Required properties must be present (a node type that requires `name` still requires it)
- Known property types are validated (if `status` is `String`, passing an `Int` still fails)
- Edge type constraints are validated (RUNS_ON must connect entity→entity)
- Node types must be declared (unknown node types still rejected)

**Rationale**: Silk is a transport and storage layer. Applications define their domain on top of Silk. An application should be able to evolve its data model (add fields, add subtypes, store metadata) without touching the ontology or recreating the store. The ontology provides guardrails (required fields, type safety for known fields, edge grammar). Everything beyond that is the application's responsibility.

**Analogy**: HTTP headers — known headers (Content-Type, Authorization) are validated by the server. Unknown headers (X-Custom-Id, X-Trace-Id) are accepted and forwarded. The protocol defines the minimum contract. Applications extend it freely.

**Impact**: Applications can now store arbitrary metadata, evolve their entity models, and introduce new subtypes without coordinating with the schema. This is critical for systems that discover new entity types at runtime (e.g., a DevOps platform discovering containers, processes, or network interfaces on managed servers).

---

## D-027: Author Authentication via ed25519 Signatures

**Status**: Implemented.

**Problem**: The `author` field in Entry is a self-declared string. Any peer can forge entries claiming any author identity. Without cryptographic authentication, Silk cannot provide provenance tracking, access control, or trust models. This limits the system to trusted peer networks.

**Design**:
- Each Silk instance holds an ed25519 keypair (generated on first boot or provided)
- `Entry` gains a `signature: Option<[u8; 64]>` field — signature over SignableContent
- `author` becomes the hex-encoded public key (32 bytes → 64 hex chars)
- Local writes: sign with the instance's private key
- Remote merge: verify signature before accepting. Invalid signatures → entry rejected.
- Key distribution: out of band (same trust model as ontology distribution)

**Wire format impact**: Breaking change. Entry struct gains a new field. Requires major version bump (v0.3.0). Old entries without signatures can be accepted via a migration flag.

**What this unlocks**:
- Provenance: "who created this entry?" is cryptographically verifiable
- Trust policies: accept entries only from known public keys
- Access control: per-author write permissions on node types
- Audit trails: every graph mutation is attributed and non-repudiable

**What this doesn't solve**: key revocation, key rotation, multi-device identity. These require a higher-level identity layer above Silk.

## D-028: Oplog Compaction (Planned — post-v0.3)

**Status**: Design exploration, not yet decided.

**Problem**: The oplog is append-only with no pruning. Every tombstone, every superseded property value, every intermediate state is retained forever. At scale (months of active editing on large graphs), this causes unbounded memory and disk growth.

**Design options under consideration**:

1. **Causal stability checkpointing**: An entry is "causally stable" when all peers have observed it (all peers' clocks are past its timestamp). Causally stable entries can be replaced with a compacted state snapshot. Requires knowing the set of active peers — hard in open networks, tractable in trusted networks.

2. **State-based snapshots**: Periodically create a synthetic "checkpoint" entry that captures the current materialized state of a subgraph. Entries prior to the checkpoint that are fully superseded can be pruned. The checkpoint becomes the new "virtual genesis" for that subgraph.

3. **Tombstone reaping**: After all peers have observed a remove_node/remove_edge entry AND the causal predecessors, the tombstone and the original add entry can both be pruned. Requires tombstone TTLs or explicit peer acknowledgment.

**Constraints**:
- Compaction must preserve hash chain integrity (entries reference parents by hash)
- Compaction must work correctly with in-flight syncs (a peer syncing from a pre-compaction state must still converge)
- If D-027 (signatures) is implemented, compacted entries must preserve or re-sign the compacted state

**Decision deferred**: Compaction is hard to get right in a CRDT system. Wrong compaction can violate convergence guarantees. Implementation should follow D-027 (signatures) because provenance tracking is needed to make safe compaction decisions.

---

*Research conducted: 2026-03-14. Based on Merkle-CRDTs (Sanjuán et al., 2020), MAPE-K (Kephart & Chess, 2003), DIKW (Zeleny 1987, Ackoff 1989), and analysis of OrbitDB, Automerge, cr-sqlite, TerminusDB, Ditto implementations. Subscription research: 2026-03-16, based on SQLite, RocksDB, Y.js, Automerge, OrbitDB, Neo4j CDC. Subtypes research: 2026-03-16, based on Google KG, Wikidata, BFO (ISO/IEC 21838-2), Neo4j, categorial grammar (Ajdukiewicz/Lambek), graph grammars (Rozenberg/Ehrig).*
