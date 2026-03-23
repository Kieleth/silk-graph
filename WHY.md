# Why Silk Exists

## The Problem

Knowledge graphs today force a choice:

**Centralized engines** (Neo4j, TigerGraph, Amazon Neptune) give you schema, queries, and algorithms — but require a server. Every write goes through a coordinator. Offline operation is not a concept. Multi-region means replication lag, conflict resolution is your problem, and the server is a single point of failure.

**Distributed CRDTs** (Automerge, Yjs, cr-sqlite) give you offline-first, conflict-free replication — but they are document-oriented or row-oriented. No schema enforcement at write time. No graph traversal primitives. You bolt on your own validation, your own BFS, your own impact analysis. The CRDT handles merge; everything else is your responsibility.

**No tool combines all five:**
1. Distributed (no server required)
2. Schema-enforced (validated at write time)
3. Graph-native (traversal, algorithms, pattern matching built in)
4. Conflict-free (mathematical convergence guarantee)
5. Provenance (ed25519 signatures — every entry is cryptographically signed)

Silk is that tool.

## What Silk Does Differently

### Schema-enforced

Silk validates every write against an ontology defined at store creation. Node types, edge types, source/target constraints, required properties, property types — all checked before the entry hits the DAG. Invalid writes are rejected, not silently stored. The ontology defines the minimum (required properties, type constraints); unknown properties are accepted (open-world, D-026). Your schema evolves without migrations.

### Conflict-free

Silk uses a Merkle-CRDT: every mutation is a content-addressed entry in a DAG, with hybrid logical clocks (R-01) for real-time causal ordering. Concurrent writes to different properties on the same node both survive — per-property last-writer-wins. Concurrent add + remove → add wins. Two stores that exchange sync messages in both directions are mathematically guaranteed to converge to the same graph state. No coordinator, no consensus protocol, no leader election.

### Offline-first

Every Silk instance is a self-contained graph database. Reads and writes work with zero network connectivity. When connectivity returns, a single sync round-trip (offer → payload → merge) brings both sides to convergence. There is no "primary" — any instance can sync with any other. The sync protocol uses Bloom filters to minimize data transfer, sending only entries the peer lacks.

### Graph-native

BFS, shortest path, impact analysis, subgraph extraction, pattern matching, topological sort, cycle detection — built into the engine, operating on the materialized graph. Not a layer on top. Not a query language that compiles to table scans. Graph structure is a first-class citizen of the storage and query model.

### Provenance

ed25519 signatures on every entry. You can verify who created each piece of data. Trust registries control which peers are accepted. Strict mode rejects unsigned entries on merge. No external PKI required — keys are generated locally and exchanged out of band (same trust model as the ontology itself).

## Proof by Example

Four example scripts demonstrate these properties with real code, real measurements, and assertions that verify correctness.

### `examples/offline_first.py` — Two-peer offline sync

Two devices each write 500 nodes independently (simulating offline operation). A single bidirectional sync merges them to 1,000 nodes on each side. Verifies identical node sets. Measures sync latency.

**What it proves:** Offline writes accumulate without conflict. Sync is a single round-trip. No server involved.

### `examples/partition_heal.py` — Three-peer partition healing

Three peers start synced, then diverge (200 nodes each, all different). After healing via mesh sync (A-B, B-C, A-C), all three converge to the same 600-node graph.

**What it proves:** Network partitions are a non-event. Diverged state merges cleanly. Convergence is independent of partition duration.

### `examples/concurrent_writes.py` — Per-property LWW

Two stores modify the same node concurrently. Store A updates `status`. Store B updates `status` AND adds `location`. After sync, both stores have the LWW winner for `status` and the non-conflicting `location` property. No data loss.

**What it proves:** Conflicts are resolved per-property, not per-node. Non-conflicting concurrent writes are never discarded.

### `examples/ring_topology.py` — Zero-coordination scale

Ten peers in a ring topology. Each writes 100 nodes. Ring sync propagates data around the ring until all 10 peers have all 1,000 nodes. Reports how many rounds were needed.

**What it proves:** Convergence works with arbitrary topologies. No peer is special. No election, no leader, no coordinator.

## Benchmarks

Measured on Apple M4 Max (16 cores, 128 GB RAM), macOS 15.7, Rust 1.94.0. Run `cargo bench --no-default-features` for your hardware.

| What | 100 nodes | 1,000 nodes | 10,000 nodes |
|------|-----------|-------------|--------------|
| Write + materialize | 129 µs | 1.5 ms | 16.8 ms |
| Sync offer generation | 24 µs | 282 µs | 3.3 ms |
| Full sync (zero overlap) | 111 µs | 1.3 ms | — |
| Incremental sync (10% delta) | — | 611 µs | — |
| Partition heal (500/side) | — | 833 µs | — |
| BFS traversal | — | 564 ns | 580 ns |
| Shortest path | — | 706 ns | 717 ns |
| Impact analysis | — | 108 ns | 105 ns |

Key takeaways:
- **Sub-millisecond sync** for typical workloads (< 1,000 divergent entries)
- **Graph algorithms don't scale with graph size** for targeted queries (BFS/shortest path access specific subgraphs)
- **Partition healing is cheap**: 500 divergent writes per side merge in 833 µs
- **2.2M entries/sec** creation throughput (449 ns/entry)

## When Silk Is the Right Tool

**Good fit:**
- **Local-first applications** — offline-capable apps that sync when connected (note-taking, task management, field data collection)
- **Edge computing** — devices that operate independently and sync periodically (IoT gateways, drones, retail POS)
- **Multi-device sync** — phone, laptop, server — all converge automatically
- **Peer-to-peer systems** — no central server, any node can sync with any other
- **Knowledge graphs with schema** — when you need both structure enforcement and graph traversal
- **Audit trails** — every change is an immutable, content-addressed entry in a Merkle-DAG

**Not the right tool:**
- **High-throughput analytics** — DuckDB, ClickHouse
- **SQL queries** — SQLite, Postgres
- **Document storage** — MongoDB, CouchDB
- **Blob storage** — S3
- **Streaming data** — Kafka, Redpanda

## Architecture

```
Write (add_node, add_edge, update_property, remove_node, remove_edge)
  |
  v
Ontology Validation
  |  reject invalid writes here, before they enter the DAG
  v
Entry { hash: BLAKE3(content), op, clock: HLC, author, parents: [head_hashes] }
  |
  v
OpLog (append-only Merkle-DAG, content-addressed, immutable)
  |
  |--- MaterializedGraph (live view)
  |      |
  |      |-- Nodes: id -> { type, label, subtype, properties }
  |      |-- Edges: id -> { type, source, target, properties }
  |      |-- Indexes: by_type, by_subtype, by_property
  |      |
  |      +-- Query / Algorithms
  |            bfs, shortest_path, impact_analysis, pattern_match,
  |            topological_sort, has_cycle, subgraph
  |
  +--- Sync Protocol
         |
         |-- generate_sync_offer()   -> Bloom filter of known hashes
         |-- receive_sync_offer()    -> entries the peer is missing
         +-- merge_sync_payload()    -> apply remote entries, re-materialize
                                        (per-property LWW, add-wins semantics)
```

**Convergence invariant:** For any two stores S1 and S2, if S1 and S2 have exchanged sync messages in both directions, then `S1.all_nodes() == S2.all_nodes()` and `S1.all_edges() == S2.all_edges()`. This holds regardless of write order, network topology, or partition duration.
