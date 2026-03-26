# Silk Experiments

Reproducible experiments that measure Silk's behavior under controlled conditions. Each experiment captures evidence (timing, payload sizes, operation counts) to guide engineering decisions.

Run all experiments: `python -m pytest experiments/ -v`
Run standalone with table output: `python experiments/test_sync_overlap.py`

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

### Fix Direction

Make the ancestor closure bloom-aware: skip parents that the remote's bloom filter says they already have. Accept the ~1% false positive rate — entries falsely skipped by the bloom will be caught by a follow-up sync round via the `need` list.

### Reproduce

```bash
python experiments/test_sync_overlap.py
```
