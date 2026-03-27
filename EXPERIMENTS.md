# Silk Experiments

Reproducible experiments that measure Silk's behavior under controlled conditions. Each experiment captures evidence (timing, payload sizes, operation counts) to guide engineering decisions.

Run all experiments: `docker run --rm silk-bench` (or `pytest experiments/ -v` locally)
Build the Docker image: `docker build -f Dockerfile.bench -t silk-bench .`
See [BENCHMARKS.md](BENCHMARKS.md#reproduction) for local setup without Docker.

---

## EXP-01: Sync Overlap Cost (F-10)

**Question:** Does sync time scale with the number of entries to send (delta), or with the number of shared entries (overlap)?

**Expected:** Sync cost should be proportional to delta. More overlap = less to send = faster sync.

**Observed:** Sync cost is proportional to overlap. More overlap = slower sync. The scaling is inverted.

### Setup

- Two peers (A, B) each with 1,000 entries
- Controlled overlap: A and B share a prefix of `N * overlap%` entries
- Measure `receive_sync_offer` time (this is where `entries_missing()` runs)
- 5 rounds per scenario, median reported

### Baseline Results (before fix)

Platform: arm64 / Darwin, Python 3.12.9, silk-graph 0.1.6

| Overlap | Shared | To Send | receive_ms | total_ms | payload_bytes |
|---------|--------|---------|------------|----------|---------------|
| 0% | 0 | 1000 | 1.24 | 6.40 | 169,295 |
| 10% | 100 | 900 | 3.91 | 9.06 | 169,808 |
| 25% | 250 | 750 | 7.68 | 12.28 | 170,398 |
| 50% | 500 | 500 | 12.92 | 16.92 | 171,346 |
| 75% | 750 | 250 | 17.60 | 21.46 | 172,756 |
| 90% | 900 | 100 | 19.82 | 23.65 | 173,895 |
| 95% | 950 | 50 | 20.68 | 24.43 | 174,128 |
| 99% | 990 | 10 | 21.47 | 25.22 | 174,339 |

**Scaling ratio (90% / 10% overlap): 5.1x** — high overlap is 5x slower despite sending 9x fewer entries.

**Payload size observation:** payload_bytes *increases* with overlap (169K → 174K). At 99% overlap, only 10 entries are unique, but the payload is larger than at 0% overlap. The ancestor closure is pulling shared entries into the payload.

### Root Cause

`entries_missing()` in `src/sync.rs:222-247` (Phase 2: ancestor closure) walks parent entries unconditionally:

```rust
// Phase 2: ancestor closure
while changed {
    changed = false;
    for entry in &all_entries {
        if !send_set.contains(&entry.hash) { continue; }
        for parent_hash in &entry.next {
            if !send_set.contains(parent_hash)
                && !remote_heads_set.contains(parent_hash)
                && oplog.get(parent_hash).is_some()
            {
                send_set.insert(*parent_hash);  // <-- adds shared entries
                changed = true;
            }
        }
    }
}
```

The closure checks if a parent is in the remote's **heads set** (usually 1-2 entries), but does NOT check if the parent is in the remote's **bloom filter** (which contains all their entries). So every parent of every entry in the send set gets pulled in, regardless of whether the bloom filter says the remote already has it.

At 90% overlap: 100 unique entries are in the send set. Their parents are shared entries. The closure adds those parents, whose parents are also shared, all the way back to genesis. The entire shared DAG (900 entries) gets re-added to the send set.

### Fix Applied

The root cause was not *what* the closure computed (it correctly identifies all needed ancestors), but *how* it computed it. The original `while changed` loop iterated over ALL entries on every pass, requiring O(depth) passes for a linear chain:

```
O(all_entries × chain_depth) = O(1000 × 900) = 900,000 iterations
```

**Fix:** Replace the nested loop with a BFS queue that processes each entry at most once:

```rust
let mut queue: VecDeque<Hash> = send_set.iter().copied().collect();
while let Some(hash) = queue.pop_front() {
    if let Some(entry) = oplog.get(&hash) {
        for parent_hash in &entry.next {
            if !send_set.contains(parent_hash)
                && !remote_heads_set.contains(parent_hash)
                && oplog.get(parent_hash).is_some()
            {
                send_set.insert(*parent_hash);
                queue.push_back(*parent_hash);
            }
        }
    }
}
```

Complexity: O(|send_set| + |ancestors|) — each entry processed once.

### Results After Fix

Platform: arm64 / Darwin, Python 3.12.9, silk-graph 0.1.6

| Overlap | Shared | To Send | receive_ms | total_ms | payload_bytes |
|---------|--------|---------|------------|----------|---------------|
| 0% | 0 | 1000 | 1.36 | 6.52 | 169,695 |
| 10% | 100 | 900 | 1.26 | 6.33 | 169,388 |
| 25% | 250 | 750 | 1.39 | 5.91 | 169,912 |
| 50% | 500 | 500 | 1.37 | 5.84 | 171,420 |
| 75% | 750 | 250 | 1.11 | 4.92 | 172,738 |
| 90% | 900 | 100 | 1.39 | 5.26 | 173,865 |
| 95% | 950 | 50 | 1.30 | 5.12 | 174,229 |
| 99% | 990 | 10 | 1.33 | 5.35 | 174,363 |

**Scaling ratio (90% / 10% overlap): 1.1x** — flat, as expected.

### Before/After Comparison

| Overlap | Before (ms) | After (ms) | Speedup |
|---------|-------------|------------|---------|
| 0% | 1.24 | 1.36 | ~same |
| 10% | 3.91 | 1.26 | 3.1x |
| 50% | 12.92 | 1.37 | 9.4x |
| 90% | 19.82 | 1.39 | 14.3x |
| 99% | 21.47 | 1.33 | 16.1x |

The `receive_ms` is now constant (~1.3ms) regardless of overlap. The improvement scales with overlap — up to **16x faster** at 99% overlap.

**Note:** Payload size is unchanged (the closure still sends all needed ancestors). The fix is purely algorithmic — same result, fewer iterations.

### Reproduce

```bash
python experiments/test_sync_overlap.py
```

---

## EXP-02: Compaction Per-Property Clock Preservation (F-11)

**Question:** Does compaction preserve enough clock metadata for correct LWW conflict resolution on individual properties?

**Expected:** A compacted peer and an uncompacted peer with concurrent writes should resolve to the same graph state as two uncompacted peers.

**Observed (before fix):** Compaction lost per-property clock granularity. A checkpoint stored one clock per entity (the max of all property clocks). After replay, all properties inherited the max clock, causing concurrent writes with clocks between the original property clocks to lose incorrectly.

### The Bug

Node `s1` has two properties with different clocks:
- `status=up` set at clock T1
- `name=beta` set at clock T5

Peer B writes `status=down` at clock T3 (where T1 < T3 < T5).

| Path | status clock | B's update clock | LWW winner | Result |
|------|-------------|-----------------|------------|--------|
| No compaction | T1 | T3 | T3 > T1, B wins | status=down (correct) |
| With compaction (before fix) | T5 (elevated) | T3 | T3 < T5, checkpoint wins | status=up (wrong) |
| With compaction (after fix) | T1 (preserved) | T3 | T3 > T1, B wins | status=down (correct) |

### Fix

Changed `build_checkpoint_ops()` to emit per-property `UpdateProperty` ops with individual clocks instead of a single `AddNode` with the entity-level max clock.

Before:
```
AddNode s1 {status=up, name=beta} @ clock=T5  ← all props get T5
```

After:
```
AddNode s1 {} @ clock=T_add        ← entity structure + add-wins clock
UpdateProperty s1.status=up @ T1   ← original property clock
UpdateProperty s1.name=beta @ T5   ← original property clock
```

### Scenarios Tested

| # | Scenario | Result |
|---|----------|--------|
| 1 | Per-property clock preservation | PASS — concurrent write wins when its clock is between two property clocks |
| 2 | Zombie resurrection (safety precondition violated) | PASS — zombie appears as expected when precondition is violated |
| 3 | Edge property clocks | PASS — edge properties preserved through compaction |
| 4 | Double compaction | PASS — compacting twice produces same state |
| 5 | Add-wins after compaction | PASS — concurrent add wins over prior remove |

### Reproduce

```bash
python experiments/test_compaction_correctness.py
pytest experiments/test_compaction_correctness.py -v
```

---

## EXP-03: Comparative CRDT Benchmarks (F-06)

**Question:** How does Silk's CRDT performance compare to other Rust+PyO3 CRDT systems on shared operations?

**Systems:** Silk (property graph), Loro (document CRDT), pycrdt/Yjs (document CRDT). All three are Rust cores with Python bindings, pip-installable, MIT-licensed.

**Scenarios:** write throughput, update throughput, sync latency, sync bandwidth, merge correctness.

Full results, methodology, and reproduction instructions: [BENCHMARKS.md](BENCHMARKS.md).

### Reproduce

```bash
python -m venv .bench-venv
source .bench-venv/bin/activate
pip install -r experiments/bench_requirements.txt
maturin develop --release  # or: pip install silk-graph
python experiments/bench_comparative.py
```

---

## EXP-04: Memory Footprint at Scale

**Question:** How much memory does Silk use as graph size grows? Is the in-memory model practical at 10K–100K nodes?

**Setup:** Realistic infrastructure graph — servers (5 properties), services (3 properties), RUNS_ON and DEPENDS_ON edges. Each server has 2-3 services, services have 1-2 dependency edges. Measured both Rust-side heap (`store.memory_usage()`) and Python-side overhead (`tracemalloc`).

### Results

| Nodes | Edges | Oplog | Graph | Rust Total | Per Node | Snapshot |
|-------|-------|-------|-------|-----------|----------|----------|
| 400 | 749 | 0.42 MB | 0.35 MB | 0.77 MB | 2.0 KB | 0.21 MB |
| 2,000 | 3,769 | 2.14 MB | 1.77 MB | 3.91 MB | 2.0 KB | 1.08 MB |
| 4,000 | 7,513 | 4.28 MB | 3.53 MB | 7.80 MB | 2.0 KB | 2.17 MB |
| 20,000 | 37,436 | 21.5 MB | 17.8 MB | 39.2 MB | 2.1 KB | 11.0 MB |
| 30,000 | 49,963 | 30.1 MB | 25.7 MB | 55.8 MB | 1.95 KB | 15.4 MB |

Python-side overhead is ~2% of Rust-side (1.14 MB at 30K nodes). Negligible.

### Observations

- **Linear scaling** — ~2 KB per node (including edges, properties, per-property clocks, adjacency indexes). 10x nodes = 10x memory.
- **Oplog is 55% of total** — the Merkle-DAG entry storage dominates. Compaction reduces this to a single checkpoint entry.
- **Projected at 100K nodes: ~186 MB** — practical on servers and laptops, tight on mobile/IoT.
- **Snapshot is ~28% of in-memory size** — MessagePack serialization is compact relative to in-memory representation.

### Reproduce

```bash
python experiments/test_memory_footprint.py
```

---

## EXP-05: Sync Payload Compression

**Question:** What is the bandwidth/latency trade-off for compressing sync payloads?

**Setup:** Two peers, sender has N entities (3 properties each). Measure full sync cycle (offer → receive → compress → decompress → merge) with different compressors. 20 rounds per configuration, median reported.

### Results (1000 entities)

| Compressor | Payload | Ratio | Sync time | Overhead |
|-----------|---------|-------|-----------|----------|
| None | 202 KB | 100% | 6.4 ms | — |
| zlib-1 | 65 KB | 32% | 8.3 ms | +29% |
| zlib-6 | 63 KB | 31% | 10.2 ms | +59% |
| zlib-9 | 63 KB | 31% | 11.7 ms | +83% |

### Observations

- **zlib-1 is the clear winner**: 68% bandwidth savings for 29% latency overhead. Higher levels give <1% extra compression at 2-3x more CPU.
- **Compression ratio is stable across scales**: ~32% at 100, 500, and 1000 entities. The MessagePack-encoded Merkle-DAG entries have consistent structure (hashes, clocks, property maps).
- **Decompression is negligible**: ~0.3ms regardless of payload size. The overhead is almost entirely in compression.

### Reproduce

```bash
python experiments/test_compression.py
```

---

## EXP-06: Fault Injection

**Question:** Does Silk converge correctly under adversarial network conditions — message loss, corruption, truncation, partitions, concurrent conflicts?

**Setup:** Two or three peers with shared history + divergent writes. Adversarial conditions applied at the transport layer (between `receive_sync_offer` and `merge_sync_payload`).

### Scenarios

| # | Scenario | Condition | Result |
|---|----------|-----------|--------|
| F1 | Message loss recovery | A→B delivered, B→A dropped. Next round recovers. | PASS — second sync round delivers missing entries |
| F2 | Duplicate delivery | Same payload merged twice | PASS — idempotent, 0 entries on second merge |
| F3 | Corrupted payload | 3 bytes flipped in payload middle | PASS — hash verification rejects corrupted entries, graph unchanged |
| F4 | Truncated payload | Payload cut to 50% | PASS — deserialization fails cleanly, graph unchanged |
| F5 | 50% random loss | Each direction has 50% delivery probability | PASS — converges within 20 rounds |
| F6 | Three-peer partition | C isolated while A-B sync, then heals | PASS — all three converge after reconnection |
| F7 | Concurrent property conflict | Two peers update same property during partition | PASS — LWW resolves deterministically, both agree |
| F8 | Rapid fire | 100 writes interleaved with syncs every 10 ops | PASS — no corruption, full convergence, integrity verified |

All scenarios verify convergence (identical node/property sets across peers) and structural integrity (`verify_integrity()` passes).

### Reproduce

```bash
python experiments/test_fault_injection.py
pytest experiments/test_fault_injection.py -v
```
