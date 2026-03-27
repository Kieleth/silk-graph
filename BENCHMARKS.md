# Comparative CRDT Benchmarks

> Measured 2026-03-26. silk-graph v0.1.5 (PyPI/crates.io), Loro 1.10.3, pycrdt 0.12.50.
> All measurements in-memory, single-threaded, same hardware.

---

## Systems Under Test

| System | Version | Data Model | CRDT Type | Language | Install |
|--------|---------|-----------|-----------|----------|---------|
| silk-graph | 0.1.5 | Property graph (Merkle-DAG oplog) | State-based (delta sync) | Rust + PyO3 | `pip install silk-graph` |
| Loro | 1.10.3 | Document (Map, List, Text, Tree) | State-based (Fugue) | Rust + PyO3 | `pip install loro` |
| pycrdt | 0.12.50 | Document (Map, Array, Text) | Op-based (YATA/Yjs) | Rust + PyO3 (Yrs) | `pip install pycrdt` |

Silk is a property-graph CRDT. Loro and pycrdt are document CRDTs. This benchmark measures shared CRDT operations (write, update, sync, merge), not data model expressiveness or domain-specific features (graph traversal, text editing, etc.).

---

## Methodology

**Hardware:** Apple M4 Max (16 cores, 128 GB RAM), macOS 15.7, Python 3.12.9.

**Procedure:** Each scenario runs 5 rounds (fresh stores per round) unless noted. Median reported. Timing via `time.perf_counter()`. All stores are in-memory (no disk I/O).

**Fairness:** Each system uses its natural API idiom:
- Silk: `add_node()` / `add_edge()` / `update_property()` / 3-phase offer/receive/merge sync
- Loro: `LoroMap.insert()` / version-vector delta export/import
- pycrdt: `Map[key] = value` / state-vector delta get_update/apply_update

Relationships modeled naturally per system: Silk uses typed graph edges; Loro and pycrdt store references as map properties.

Adapters normalize the interface without altering each system's internal behavior. Source: [`experiments/adapters.py`](experiments/adapters.py).

---

## Results

### S1: Write Throughput

Create N entities, each with 3 properties (`name: str`, `status: str`, `seq: int`).

| System | N=100 | N=1,000 | N=10,000 |
|--------|-------|---------|----------|
| silk | 0.36 ms (278K ops/s) | 3.95 ms (253K ops/s) | 47.66 ms (210K ops/s) |
| loro | 0.28 ms (357K ops/s) | 3.17 ms (315K ops/s) | 31.22 ms (320K ops/s) |
| pycrdt | 0.47 ms (213K ops/s) | 4.07 ms (246K ops/s) | 43.64 ms (229K ops/s) |

### S2: Update Throughput

Update one field on a single entity N times.

| System | N=100 | N=1,000 | N=10,000 |
|--------|-------|---------|----------|
| silk | 0.18 ms (556K ops/s) | 1.86 ms (538K ops/s) | 18.96 ms (527K ops/s) |
| loro | 0.10 ms (1.0M ops/s) | 0.98 ms (1.0M ops/s) | 10.44 ms (958K ops/s) |
| pycrdt | 0.30 ms (333K ops/s) | 2.92 ms (342K ops/s) | 31.03 ms (322K ops/s) |

### S3: Sync Latency

Two peers each write M unique entities, then sync bidirectionally. Includes store creation and write time.

| System | M=100 | M=500 |
|--------|-------|-------|
| silk | 1.98 ms | 10.98 ms |
| loro | 0.94 ms | 5.50 ms |
| pycrdt | 1.31 ms | 7.44 ms |

### S4: Sync Bandwidth

Bytes transferred for bidirectional sync of M entities (3 properties each).

| System | M=100 (total) | M=500 (total) |
|--------|--------------|--------------|
| silk | 34,549 | 175,143 |
| loro | 4,692 | 25,494 |
| pycrdt | 6,631 | 36,232 |

### S5: Merge Correctness

Fork from shared state, concurrent update to the same field, bidirectional sync, verify both peers converge to the same value. 10 rounds.

| System | Converged | Rate |
|--------|-----------|------|
| silk | 10/10 | 100% |
| loro | 10/10 | 100% |
| pycrdt | 10/10 | 100% |

### S6: Structured Workload

Users + projects + assignments + status updates. Each user assigned to 1–3 projects, then all project statuses updated.

| System | 50 users / 10 projects | 200 / 40 | 1000 / 200 |
|--------|----------------------|----------|------------|
| silk | 0.67 ms (257K ops/s) | 2.32 ms (289K ops/s) | 11.84 ms (289K ops/s) |
| loro | 0.42 ms (408K ops/s) | 2.13 ms (315K ops/s) | 7.95 ms (430K ops/s) |
| pycrdt | 4.15 ms (41K ops/s) | 71.25 ms (9K ops/s) | 2,436 ms (1.4K ops/s) |

Snapshot sizes at 1000 users / 200 projects:

| System | Snapshot |
|--------|---------|
| silk | 641 KB |
| loro | 69 KB |
| pycrdt | 92 KB |

### S7: Multi-Peer Ring Convergence

N peers each write unique entities, then ring-sync (0→1→2→...→N-1→0) until converged.

| System | 3 peers × 100 | 5 × 100 | 10 × 50 |
|--------|--------------|---------|---------|
| silk | 7.2 ms / 6 rounds / 119 KB | 30.8 ms / 10 rounds / 394 KB | 111 ms / 20 rounds / 898 KB |
| loro | 1.5 ms / 6 rounds / 19 KB | 5.1 ms / 10 rounds / 62 KB | 11.6 ms / 20 rounds / 140 KB |
| pycrdt | 8.1 ms / 6 rounds / 28 KB | 43.5 ms / 10 rounds / 95 KB | 188 ms / 20 rounds / 211 KB |

All systems converge in the same number of rounds (2 × N for ring topology).

### S8: Diverge-Then-Heal

Two peers fork from shared state, each writes independently, then sync to heal the partition.

| System | 100 shared + 50 divergent | 500 + 200 | 1000 + 500 |
|--------|--------------------------|-----------|------------|
| silk | 2.53 ms / 55 KB | 25.2 ms / 261 KB | 93.6 ms / 559 KB |
| loro | 0.27 ms / 2 KB | 1.21 ms / 10 KB | 3.56 ms / 26 KB |
| pycrdt | 0.68 ms / 3 KB | 2.94 ms / 14 KB | 9.07 ms / 36 KB |

---

## Analysis

### Per-operation cost

Silk creates a content-addressed Merkle-DAG entry for every mutation: BLAKE3 hash, HLC clock, author identity, parent links, MessagePack serialization. This is the fixed cost of immutable, auditable, causally ordered operations.

| System | Write ops/s (N=1K) | Update ops/s (N=1K) | Update/Write ratio |
|--------|-------------------|--------------------|--------------------|
| silk | 253K | 538K | 2.1x |
| loro | 315K | 1.0M | 3.2x |
| pycrdt | 246K | 342K | 1.4x |

Loro's updates are in-place map mutations with near-zero per-operation overhead until commit. Silk's updates still create DAG entries but skip graph node creation. pycrdt's ratio is low because its writes are already lightweight (no per-write hashing).

### Write scaling (S1)

| System | N=100 ops/s | N=10K ops/s | Degradation |
|--------|-------------|-------------|-------------|
| silk | 278K | 210K | -24% |
| loro | 357K | 320K | -10% |
| pycrdt | 213K | 229K | flat |

Silk degrades slightly as the oplog grows — HashMap lookup and head tracking cost increases with entry count. Loro and pycrdt maintain throughput.

### Structured workload scaling (S6)

pycrdt's throughput drops from 213K ops/s (S1 flat writes) to 1.4K ops/s (S6 at 1000 users). Creating 1,200+ top-level maps causes per-operation overhead to grow non-linearly. Silk and Loro maintain throughput — Silk at 289K ops/s, Loro at 430K ops/s. For workloads with many distinct entities and relationships, pycrdt's architecture is not suited.

### Sync bandwidth

| System | Bytes per entity (M=500) | Relative to Loro |
|--------|-------------------------|------------------|
| loro | 25 | 1.0x |
| pycrdt | 36 | 1.4x |
| silk | 175 | 6.9x |

This ratio is consistent across all scenarios (S4, S7, S8). Each Silk sync entry carries:
- 32 bytes BLAKE3 content hash
- HLC clock (instance_id string + physical_ms + logical)
- Author identity string
- Parent hash links (Merkle-DAG causal chain)
- MessagePack envelope

Loro and pycrdt send compact CRDT deltas without per-operation identity, integrity hashes, or causal links.

### Sync compute efficiency (S3 vs S4)

| System | Sync latency (M=500) | Bandwidth (M=500) | Throughput |
|--------|---------------------|-------------------|------------|
| silk | 11.0 ms | 175 KB | 15.9 KB/ms |
| loro | 5.5 ms | 25 KB | 4.6 KB/ms |
| pycrdt | 7.4 ms | 36 KB | 4.9 KB/ms |

Silk processes 3x more bytes per millisecond. The latency gap is dominated by serialization volume, not compute.

### Multi-peer scaling (S7)

All systems need the same number of sync rounds for ring convergence (2 × peer count — information propagates one hop per round). The per-round cost is proportional to each system's sync latency. At 10 peers, pycrdt (188ms) is slower than Silk (111ms) due to per-sync overhead accumulation.

### Partition heal cost (S8)

At 1000 shared + 500 divergent entries per peer, Silk heals in 94ms transferring 559 KB. Loro heals in 3.6ms transferring 26 KB. The 22x bandwidth ratio is higher than the 7x in S4 because S8's payload includes ancestor closure metadata for the shared prefix.

For context: a 1,500-entity partition heal in 94ms is practical for any sync interval above ~200ms. The bandwidth (559 KB) is a single HTTP response on any modern connection.

### What Silk's overhead buys

None of the comparison systems provide these capabilities:

| Capability | Silk | Loro | pycrdt |
|-----------|------|------|--------|
| Content-addressed entries (tamper detection, deduplication) | Yes | No | No |
| Causal ordering via Merkle-DAG (happened-before relation) | Yes | No | No |
| Immutable audit trail (every mutation is a permanent entry) | Yes | No | No |
| Schema enforcement at write time (typed nodes, edges, properties) | Yes | No | No |
| Graph structure (typed edges, BFS, shortest path, pattern match) | Yes | No | No |
| Author authentication (ed25519 signatures per entry) | Yes | No | No |

These are architectural properties, not benchmarkable as throughput. The bandwidth and latency overhead is the cost of carrying per-operation integrity, identity, and causal structure.

### Practical context

At 10,000 entities with 3 properties each, Silk writes the full graph in **48ms**. For the use cases Silk targets — infrastructure graphs, configuration sync, knowledge graphs syncing between devices or services — these numbers are within practical bounds:

- A 500-server infrastructure graph (servers, services, edges, properties): well under 10K entities. Full write in under 50ms.
- Periodic sync between two peers with 500 divergent entities: **11ms**. At sync intervals of 1–10 seconds, this is <1% of the sync window.
- A 1,500-entity partition heal (1000 shared + 500 divergent): **94ms**. Imperceptible in any system that syncs on a timer.
- A 10K-entity sync over WAN: Silk transfers ~3.5 MB vs Loro's ~500 KB. On a 10 Mbps link, that's 2.8 seconds vs 0.4 seconds. On LAN, the difference is imperceptible.

The bandwidth gap is the primary engineering trade-off. For metered or highly constrained connections, payload compression (gzip/zstd over the MessagePack entries) or compact delta encoding are future optimization paths.

---

## Limitations

This benchmark does **not** measure:
- **Text editing** — Silk has no sequence CRDT. Loro and pycrdt are designed for collaborative text. Comparing text operations would be misleading.
- **Graph operations** — Silk provides BFS, shortest path, impact analysis, pattern matching, and schema-enforced traversal. The comparison systems have no graph primitives. Comparing graph queries would be equally one-sided.
- **Persistent storage** — all measurements are in-memory. Disk I/O characteristics differ across systems.
- **Network latency** — sync measures serialization + merge cost, not TCP round trips.
- **Memory usage** — not instrumented in this pass.
- **Automerge** — excluded because the Python bindings (v0.1.2) do not support mutation or merge operations needed for benchmarking.

---

## Reproduction

```bash
# Create isolated environment
python -m venv .bench-venv
source .bench-venv/bin/activate
pip install -r experiments/bench_requirements.txt

# Build silk from source (or: pip install silk-graph)
maturin develop --release

# Run all benchmarks
python experiments/bench_comparative.py

# Run a single scenario
python experiments/bench_comparative.py --scenario=S6

# Run specific systems
python experiments/bench_comparative.py --only=silk,loro

# JSON output
python experiments/bench_comparative.py --json

# Correctness test only
pytest experiments/bench_comparative.py -v
```

Source: [`experiments/bench_comparative.py`](experiments/bench_comparative.py), [`experiments/adapters.py`](experiments/adapters.py).
