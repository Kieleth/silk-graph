# Comparative CRDT Benchmarks

Measurements of shared CRDT operations across three systems. Each system uses its natural API. All measurements are in-memory, single-threaded, on the same hardware.

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

**Procedure:** Each scenario runs 5 rounds (fresh stores per round). Median reported. Timing via `time.perf_counter()`. No warm-up for sync scenarios (each round is independent). All stores are in-memory (no disk I/O).

**Fairness:** Each system uses its natural API idiom:
- Silk: `add_node()` / `update_property()` / 3-phase offer/receive/merge sync
- Loro: `LoroMap.insert()` / version-vector delta export/import
- pycrdt: `Map[key] = value` / state-vector delta get_update/apply_update

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

Two peers each write M unique entities, then sync bidirectionally (A→B, B→A). Includes store creation and write time.

| System | M=100 | M=500 |
|--------|-------|-------|
| silk | 1.98 ms | 10.98 ms |
| loro | 0.94 ms | 5.50 ms |
| pycrdt | 1.31 ms | 7.44 ms |

### S4: Sync Bandwidth

Bytes transferred for bidirectional sync of M entities (3 properties each).

| System | M=100 (A→B / B→A / total) | M=500 (A→B / B→A / total) |
|--------|---------------------------|---------------------------|
| silk | 17,256 / 17,293 / 34,549 | 87,531 / 87,612 / 175,143 |
| loro | 2,346 / 2,346 / 4,692 | 12,747 / 12,747 / 25,494 |
| pycrdt | 3,316 / 3,315 / 6,631 | 18,116 / 18,116 / 36,232 |

### S5: Merge Correctness

Fork from shared state, concurrent update to the same field, bidirectional sync, verify both peers converge to the same value. 10 rounds.

| System | Converged | Rate |
|--------|-----------|------|
| silk | 10/10 | 100% |
| loro | 10/10 | 100% |
| pycrdt | 10/10 | 100% |

---

## Observations

**Write throughput (S1):** All three systems operate in the same order of magnitude (200K–350K ops/sec at N=10K). Loro maintains throughput as N grows; Silk and pycrdt show slight sub-linear scaling.

**Update throughput (S2):** Loro is approximately 2x the throughput of Silk and 3x pycrdt for single-field updates. Loro's update path has less per-operation overhead (no oplog entry creation, no hash computation).

**Sync latency (S3):** Loro is the fastest sync at both scales. Silk's 3-phase sync protocol (offer/receive/merge with Bloom filter) adds overhead compared to Loro's version-vector delta export and pycrdt's state-vector diff. The overhead is constant per sync round, not per entry.

**Sync bandwidth (S4):** Silk transfers 5–7x more bytes than Loro and pycrdt. Silk's sync payload includes full Merkle-DAG entries (hash, clock, author, payload, parent links) — content-addressed entries carry more metadata than document CRDT deltas. This is the cost of content addressing and causal ordering in the sync payload.

**Merge correctness (S5):** All three systems achieve 100% convergence on concurrent updates. This is expected — all are mathematically convergent CRDTs.

---

## Limitations

This benchmark does **not** measure:
- **Text editing** — Silk has no sequence CRDT. Loro and pycrdt excel at collaborative text. Comparing text operations would be misleading.
- **Graph traversal** — Silk has BFS, shortest path, pattern matching. The others do not. Comparing graph queries would be equally misleading.
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

# Run benchmarks
python experiments/bench_comparative.py

# JSON output
python experiments/bench_comparative.py --json

# Run specific system
python experiments/bench_comparative.py --only=silk,loro

# Run correctness test only
pytest experiments/bench_comparative.py -v
```

Source: [`experiments/bench_comparative.py`](experiments/bench_comparative.py), [`experiments/adapters.py`](experiments/adapters.py).
