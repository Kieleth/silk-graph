# Silk

A Merkle-CRDT graph engine for distributed, conflict-free knowledge graphs.

[![CI](https://github.com/Kieleth/silk-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/Kieleth/silk-graph/actions/workflows/ci.yml)
[![crates.io](https://img.shields.io/crates/v/silk-graph.svg)](https://crates.io/crates/silk-graph)
[![PyPI](https://img.shields.io/pypi/v/silk-graph.svg)](https://pypi.org/project/silk-graph/)
[![License](https://img.shields.io/badge/license-MIT%2FApache--2.0-blue.svg)](LICENSE-MIT)

Silk is an embedded graph database with automatic conflict resolution. Built on Merkle-DAGs and CRDTs, it requires no leader, no consensus protocol, and no coordinator. Any two Silk instances that exchange sync messages are guaranteed to converge to the same graph state. Schema is enforced at write time via an ontology — not at query time.

## Quick Start

### Python

```bash
pip install silk-graph
```

```python
from silk import GraphStore
import json

# Define your schema
ontology = json.dumps({
    "node_types": {
        "person": {"properties": {"age": {"value_type": "int"}}},
        "company": {"properties": {}}
    },
    "edge_types": {
        "WORKS_AT": {"source": "person", "target": "company", "properties": {}}
    }
})

# Create two independent stores (imagine different machines)
store_a = GraphStore("node-a", ontology)
store_b = GraphStore("node-b", ontology)

# Write to store A
store_a.add_node("alice", "person", "Alice", {"age": 30})
store_a.add_node("acme", "company", "Acme Corp")
store_a.add_edge("e1", "WORKS_AT", "alice", "acme")

# Write to store B (concurrently, no coordination)
store_b.add_node("bob", "person", "Bob", {"age": 25})

# Sync: A sends to B
offer = store_a.generate_sync_offer()
payload = store_b.receive_sync_offer(offer)
store_a.merge_sync_payload(payload)

# Sync: B sends to A
offer = store_b.generate_sync_offer()
payload = store_a.receive_sync_offer(offer)
store_b.merge_sync_payload(payload)

# Both stores now have Alice, Bob, Acme, and the WORKS_AT edge
assert store_a.get_node("alice") is not None
assert store_a.get_node("bob") is not None
assert store_b.get_node("alice") is not None
assert store_b.get_node("bob") is not None
```

### Rust

```rust
use silk::{GraphStore, Ontology};

let ontology = Ontology::from_json(schema_json)?;
let mut store = GraphStore::new("node-1", ontology);

store.add_node("alice", "person", "Alice", Some(props))?;
store.add_edge("e1", "WORKS_AT", "alice", "acme", None)?;

// Sync with a peer
let offer = store.generate_sync_offer();
let payload = peer.receive_sync_offer(&offer)?;
store.merge_sync_payload(&payload)?;
```

## Features

- **Ontology-enforced schema** — define node types, edge types, and their properties. Silk validates every write against the schema. Invalid operations are rejected, not silently accepted.
- **Content-addressed entries** — every mutation is a BLAKE3-hashed entry in a Merkle-DAG. Entries are immutable. The DAG is the audit trail.
- **Per-property last-writer-wins** — two concurrent writes to different properties on the same node both succeed. No data loss from non-conflicting edits.
- **Delta-state sync** — Bloom filter optimization minimizes data transfer. Only entries the peer doesn't have are sent.
- **Graph algorithms** — BFS, shortest path, impact analysis, pattern matching, topological sort, cycle detection. Built into the engine, not bolted on.
- **Persistent storage** — backed by [redb](https://github.com/cberner/redb) (embedded, transactional, pure Rust). In-memory mode also available.
- **Real-time subscriptions** — register callbacks that fire on every graph mutation (local or merged from sync).
- **Observation log** — append-only, TTL-pruned time-series store for metrics alongside the graph. Same redb backend.
- **Zero runtime dependencies** — no Postgres, no Redis, no network required. Silk is a library, not a service.

## When to Use Silk

**Good fit:**
- Local-first applications (offline-capable, sync when connected)
- Edge computing (devices that operate independently, sync periodically)
- Peer-to-peer systems (no central server, any node can sync with any other)
- Knowledge graphs with schema enforcement
- Multi-device sync (phone, laptop, server — all converge)
- Systems that need an audit trail (every change is a Merkle-DAG entry)

**Not the right tool:**
- High-throughput analytics — use DuckDB or ClickHouse
- SQL queries — use SQLite or Postgres
- Document storage — use MongoDB or CouchDB
- Blob storage — use S3

## Architecture

```
Write (add_node, add_edge, update_property)
  │
  ▼
Entry { hash(BLAKE3), op, clock(Lamport), author, parents }
  │
  ▼
OpLog (append-only Merkle-DAG, content-addressed)
  │
  ├──► MaterializedGraph (live view: nodes, edges, properties)
  │    └── Query: get_node, query_by_type, outgoing_edges, bfs, shortest_path
  │
  └──► Sync Protocol
       ├── generate_sync_offer()  →  Bloom filter of known hashes
       ├── receive_sync_offer()   →  Entries the peer is missing
       └── merge_sync_payload()   →  Apply remote entries, re-materialize
```

**Convergence guarantee:** Two stores that have exchanged sync messages in both directions will have identical materialized graphs. This is a mathematical property of the Merkle-CRDT construction, not an implementation detail.

## Design Decisions

Silk's architecture is driven by 25 explicit design decisions (D-001 through D-025), documented in full in [DESIGN.md](DESIGN.md). Key choices:

| Decision | Choice | Why |
|----------|--------|-----|
| Hash function | BLAKE3 | Fastest cryptographic hash, 128-bit security |
| Serialization | MessagePack | Compact binary, faster than JSON, schema-free |
| Storage | redb | Embedded, transactional, pure Rust, no C dependencies |
| Clock | Lamport | Sufficient for causality ordering without wall-clock sync |
| Conflict resolution | Per-property LWW | Non-conflicting concurrent writes both win |
| Sync | Delta-state + Bloom | Minimize transfer: only send what the peer lacks |

## Python API Reference

### GraphStore

```python
# Construction
store = GraphStore(instance_id, ontology_json, path=None)  # new store
store = GraphStore.open(path)                               # existing store

# Mutations
store.add_node(node_id, node_type, label, properties=None, subtype=None)
store.add_edge(edge_id, edge_type, source_id, target_id, properties=None)
store.update_property(entity_id, key, value)
store.remove_node(node_id)
store.remove_edge(edge_id)

# Queries
store.get_node(node_id)          # dict | None
store.get_edge(edge_id)          # dict | None
store.query_nodes_by_type(t)     # list[dict]
store.query_nodes_by_subtype(s)  # list[dict]
store.all_nodes()                # list[dict]
store.all_edges()                # list[dict]
store.outgoing_edges(node_id)    # list[dict]
store.incoming_edges(node_id)    # list[dict]

# Graph algorithms
store.bfs(start, max_depth=None, edge_type=None)
store.shortest_path(start, end)
store.impact_analysis(node_id, max_depth=None)
store.pattern_match(type_sequence)
store.topological_sort()
store.has_cycle()

# Sync
offer = store.generate_sync_offer()          # bytes
payload = store.receive_sync_offer(offer)     # bytes
count = store.merge_sync_payload(payload)     # int (entries merged)
snapshot = store.snapshot()                    # bytes (full state)

# Subscriptions
sub_id = store.subscribe(callback)  # callback(event_dict)
store.unsubscribe(sub_id)
```

### ObservationLog

```python
from silk import ObservationLog

log = ObservationLog(path, max_age_secs=86400)
log.append(source="cpu", value=45.2, metadata={"host": "srv-1"})
log.query(source="cpu", since_ts_ms=1710000000000)
log.query_latest(source="cpu")
log.truncate(before_ts_ms=1710000000000)
```

## Building from Source

```bash
# Rust tests
cargo test --all-features

# Python development build
pip install maturin
maturin develop --release

# Python tests
pytest pytests/

# Benchmarks
cargo bench
```

## License

Licensed under either of:

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
