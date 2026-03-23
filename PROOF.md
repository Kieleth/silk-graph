# Silk Convergence Proof

A semi-formal proof that Silk's Merkle-CRDT guarantees convergence. Not machine-verified — precise enough to be mechanically verifiable later.

**Claim**: Two Silk stores that exchange sync messages in both directions will have identical materialized graphs.

**References**:
- Shapiro, Preguiça, Baquero & Zawirski (2011) — *Conflict-free Replicated Data Types*
- Kleppmann, Gomes, Mulligan & Beresford (2017) — Formal verification of Automerge's OpSet
- Hellerstein & Alvaro (2020) — CALM theorem: monotonic programs converge without coordination

---

## 1. Definitions

### Entry

An entry `e` is a tuple `(hash, payload, next, refs, clock, author, signature)` where:
- `hash = BLAKE3(msgpack(payload, next, refs, clock, author))` — content address
- `payload` ∈ GraphOp — the graph mutation
- `next` ⊆ Hashes — causal predecessors (DAG parents)
- `clock` = HybridClock(id, physical_ms, logical) — hybrid logical clock
- `author` ∈ String — instance identifier

Two entries are equal iff their hashes are equal (content-addressed identity).

*Reference*: `src/entry.rs:72-89`

### OpLog

An OpLog `L` is a set of entries forming a directed acyclic graph (DAG) via the `next` field. It has:
- `L.entries`: HashMap<Hash, Entry> — the set of all entries
- `L.heads`: the set of entries with no successors
- `L.genesis`: the unique entry with `next = []` (the DefineOntology entry)

Invariants:
- For every entry `e ∈ L`, all hashes in `e.next` are keys in `L.entries` (causal completeness)
- `L.genesis` exists and is unique
- `L.heads` = {e ∈ L | ∄ e' ∈ L : e.hash ∈ e'.next}

*Reference*: `src/oplog.rs:10-40`

### Topological Order

A topological order `T(L)` of an OpLog `L` is a sequence of all entries in `L` such that:
- For every entry `e`, all entries in `e.next` appear before `e` in the sequence
- Entries with the same in-degree are sorted by `(clock.physical_ms, clock.logical, hash)`

This ordering is deterministic: given the same set of entries, `T(L)` is identical regardless of insertion order.

*Reference*: `src/oplog.rs:125-185`

### HybridClock Total Order

The total order `≤_c` on HybridClocks is defined by:

```
a ≤_c b  ⟺  (a.physical_ms, a.logical, reverse(a.id)) ≤ (b.physical_ms, b.logical, reverse(b.id))
```

Where `reverse(id)` means lexicographically lower id is "greater" (wins ties).

Properties:
- **Reflexive**: a ≤_c a
- **Antisymmetric**: a ≤_c b ∧ b ≤_c a → a = b
- **Transitive**: a ≤_c b ∧ b ≤_c c → a ≤_c c
- **Total**: ∀ a,b: a ≤_c b ∨ b ≤_c a

*Reference*: `src/clock.rs:99-104`

### MaterializedGraph

A materialized graph `G` derived from an OpLog `L` consists of:
- `G.nodes`: HashMap<String, Node>
- `G.edges`: HashMap<String, Edge>
- `G.quarantined`: HashSet<Hash>

Where `G = Materialize(T(L))` — apply each entry in topological order.

*Reference*: `src/graph.rs:56-69`

### clock_wins

```
clock_wins(new, existing) = new >_c existing
```

Strict greater-than in the HybridClock total order.

*Reference*: `src/graph.rs:470-472`

---

## 2. Invariants

### I-01: Hash Integrity

For every entry `e`, `e.hash = BLAKE3(msgpack(e.payload, e.next, e.refs, e.clock, e.author))`.

If this does not hold, `verify_hash()` returns false and the entry is rejected by `append()`.

*Reference*: `src/entry.rs:196-205`, `src/oplog.rs:46-48`

### I-02: Causal Completeness

For every entry `e ∈ L`, for every hash `h ∈ e.next`, `h ∈ L.entries`.

Enforced by `append()`: entries with missing parents are rejected with `MissingParent`.

*Reference*: `src/oplog.rs:55-62`

### I-03: Append-Only

Entries are never removed from `L.entries`. The set only grows.

`append()` inserts into a HashMap but never removes.

*Reference*: `src/oplog.rs:44-75`

### I-04: Heads Accuracy

`L.heads = {e ∈ L | ∄ e' ∈ L : e.hash ∈ e'.next}`

Updated atomically on each `append()`: new entry added to heads, its parents removed.

*Reference*: `src/oplog.rs:63-71`

### I-05: Topological Determinism

For any set of entries `S`, `T(S)` is a unique sequence determined entirely by:
1. The DAG structure (next links)
2. The deterministic tiebreaker `(clock.physical_ms, clock.logical, hash)`

Two implementations with the same entry set produce the same topological order.

*Reference*: `src/oplog.rs:125-185`

### I-06: Quarantine Monotonicity

`G.quarantined` is a grow-only set. Entries are added on validation failure but never removed.

*Reference*: `src/graph.rs:72, 102, 117, 140`

---

## 3. Theorems

### Theorem 1: Deterministic Materialization

**Statement**: For any OpLog `L`, `Materialize(T(L))` produces a unique, deterministic MaterializedGraph `G`.

**Proof sketch**:

1. `T(L)` is deterministic (I-05) — same entry set → same sequence.

2. Each `apply(entry)` is a deterministic function of the entry and current graph state:
   - **AddNode**: For a given node_id, the final state after all AddNode entries is determined by the entry with the highest clock (clock_wins). Since ≤_c is a total order, there is exactly one winner. Per-property LWW is deterministic per key.
   - **AddEdge**: Same logic as AddNode.
   - **UpdateProperty**: Per-property LWW — each property key has exactly one winning clock value.
   - **RemoveNode/RemoveEdge**: Tombstoned iff the remove clock is strictly greater than last_add_clock. Since ≤_c is total, this is deterministic.
   - **ExtendOntology**: `merge_extension()` is deterministic (BTreeMap operations are ordered).
   - **Quarantine**: Validation against the ontology is deterministic.

3. Because T(L) is unique and each apply() is deterministic, G = Materialize(T(L)) is unique. ∎

### Theorem 2: Idempotent Merge

**Statement**: If entry `e` is already in OpLog `L`, then `append(L, e)` leaves `L` unchanged.

**Proof sketch**:

1. `append()` checks `L.entries.contains_key(e.hash)` (line 50).
2. If the hash exists, it returns `Ok(false)` without mutation.
3. The materialized graph is unchanged because `apply()` is only called for newly inserted entries. ∎

**Corollary**: Syncing the same entries twice is a no-op. After the first sync, `entries_missing()` returns an empty payload, and `merge_entries()` inserts zero entries.

### Theorem 3: Convergence After Bidirectional Sync

**Statement**: Let `L_A` and `L_B` be two OpLogs sharing the same genesis. After bidirectional sync (A→B then B→A), both OpLogs contain the same set of entries, and both materialized graphs are identical.

**Proof sketch**:

**Step 1: Sync A→B delivers all entries A has that B lacks.**

The sync protocol works in three phases:

- **Phase 1 (Bloom filter)**: A collects all local entries not in B's Bloom filter. False negatives are impossible (Bloom property). False positives may exclude entries B actually needs.

- **Phase 1.5 (Force heads)**: For each head in A that is NOT in B's head set, A forces it into the send set. This handles the critical case where a Bloom false positive hits a DAG tip — since tips have no descendants, ancestor closure cannot recover them.

- **Phase 2 (Ancestor closure)**: A walks the ancestors of every entry in the send set. If an ancestor is not in B's heads and not already in the send set, it's added. This repeats until no new ancestors are found. This ensures causal completeness: every entry sent has all its parents either already on B or included in the payload.

*Reference*: `src/sync.rs:170-248`

After A→B sync: `L_B.entries ⊇ L_B_before.entries ∪ (L_A.entries \ L_B_before.entries)` — B gains all entries A had.

**Step 2: Sync B→A delivers all entries B has that A lacks.**

By the same protocol, A receives all entries B had (including entries B received in Step 1, if B had unique entries).

After B→A sync: `L_A.entries ⊇ L_A_before.entries ∪ L_B.entries`

**Step 3: Both OpLogs are equal.**

After bidirectional sync:
- `L_A.entries = L_A_before ∪ L_B_before` (A has everything)
- `L_B.entries = L_A_before ∪ L_B_before` (B has everything)
- Therefore `L_A.entries = L_B.entries`

**Step 4: Identical entries → identical materialized graphs.**

By Theorem 1: same entry set → same topological order → same materialized graph.

Therefore `G_A = Materialize(T(L_A)) = Materialize(T(L_B)) = G_B`. ∎

---

## 4. Quarantine Addendum

**Claim**: Quarantine does not affect OpLog convergence.

**Proof**: Quarantine operates at the MaterializedGraph layer, not the OpLog layer. `graph.apply()` adds entries to the quarantined set; the OpLog is unaffected. Since OpLog convergence (Theorem 3) depends only on entry sets, and quarantine does not add or remove entries from the OpLog, convergence is preserved.

**Note**: Two peers may have different quarantined sets if they have different ontologies (due to `ExtendOntology` ordering). The OpLogs are identical; the materialized graphs may differ. This is by design — quarantine is local policy (R-02).

---

## 5. Ontology Evolution Addendum

**Claim**: Concurrent `ExtendOntology` operations converge correctly.

**Case 1: Different type names.** Peer A adds type "alpha", Peer B adds type "beta". After sync, both OpLogs contain both extensions. During materialization, each `ExtendOntology` entry is applied in topological order. Since they add different keys, `merge_extension()` succeeds on both peers. Both ontologies contain "alpha" and "beta".

**Case 2: Same type name.** Peer A adds type "shared" with properties X, Peer B adds type "shared" with properties Y. After sync, both OpLogs contain both entries. During materialization, the first-applied extension succeeds; the second is quarantined (R-02) because the type already exists. Both peers quarantine the later entry (by topological order, which is deterministic). The ontology contains the first-applied definition of "shared".

**Case 3: Extension depends on prior extension.** Peer A extends the ontology, then uses the new type. These entries have a causal link (next). After sync, B receives both entries. Topological order ensures the extension is applied before the data entry. The data entry validates against the evolved ontology.

In all cases, OpLogs converge (Theorem 3). Materialized graphs converge for the non-quarantined subset. ∎

---

## 6. Conflict Resolution Properties

### Per-Property Last-Writer-Wins

For any node and property key `k`:

```
final_value(k) = value from the entry with max clock among all entries that set k
```

Since the clock total order is deterministic, `max` has exactly one winner. Concurrent updates to different keys do not interfere (different LWW channels).

### Add-Wins Semantics

For any node:

```
tombstoned = last_remove_clock >_c last_add_clock
```

If add and remove have the same clock (different authors), `>_c` resolves deterministically via author ID tiebreaker. In practice, concurrent add+remove from different peers always resolves to "exists" because the add's `last_add_clock` is set by the add, and the remove must be strictly greater to win.

---

## 7. Assumptions

This proof assumes:

1. **No hash collisions**: BLAKE3 provides 128-bit collision resistance. Two different entries producing the same hash is computationally infeasible.

2. **Correct MessagePack serialization**: `rmp_serde` produces identical bytes for identical Rust structs across platforms. This is guaranteed by serde's deterministic serialization of `#[derive(Serialize)]` structs with ordered maps (BTreeMap).

3. **No Byzantine peers** (for materialized graph convergence): A peer that forges entries with manipulated clocks can win LWW conflicts unfairly. OpLog convergence still holds (entries converge regardless of content), but graph semantics may be incorrect. Author signatures (D-027) mitigate this.

4. **Reliable delivery**: The sync protocol assumes messages are delivered intact. Corruption is detected by hash verification.

---

## 6. Compaction Addendum (R-08)

**Claim**: A compacted OpLog converges with uncompacted peers.

**Proof sketch**:

1. A checkpoint at time T is safe iff ALL known peers have synced past T. This means no peer holds entries concurrent with or before T that haven't been seen by the compacting peer.

2. The checkpoint entry contains synthetic ops (DefineOntology + AddNode + AddEdge) that, when replayed, produce a MaterializedGraph identical to the pre-compaction graph. This follows from the checkpoint construction: it iterates all live nodes and edges and emits the corresponding ops.

3. After compaction, the OpLog contains exactly one entry (the checkpoint). New writes have the checkpoint as their parent.

4. When an uncompacted peer syncs with a compacted peer:
   - Delta sync may fail (old parent hashes missing). Fallback to snapshot bootstrap.
   - Snapshot from checkpoint produces the same graph as the full history (by construction).
   - After snapshot bootstrap + delta sync of post-compaction entries, both peers converge.

5. Two compacted peers with checkpoints at different times still converge, because:
   - Both checkpoints capture the same logical state (safety rule ensures all entries were synced)
   - Post-checkpoint entries are identical (same sync protocol)
   - Materialization is deterministic (Theorem 1)

Therefore, compaction preserves convergence. ∎

---

## 8. What This Proof Does NOT Cover

- **Liveness**: This proof shows convergence (safety), not that sync will eventually complete (liveness). Liveness depends on network connectivity and the gossip protocol (R-05).

- **Byzantine fault tolerance**: A malicious peer can inject valid-looking entries with spoofed clocks. Signatures (D-027) authenticate authors but don't prevent a compromised key from issuing bad data. Full BFT requires trust policies and quorum mechanisms beyond Silk's scope.

- **Performance bounds**: This proof shows correctness, not efficiency. Sync may transfer redundant entries (Bloom false positives); ancestor closure may be slow on adversarial DAGs. These are performance issues, not correctness issues.

---

*Proof structure follows Shapiro et al. (2011) Section 3.2: state-based CRDT convergence via join-semilattice properties. The OpLog entry set forms a join-semilattice under set union. Materialization is a monotonic function from the lattice to the graph domain. Quarantine and ontology evolution preserve monotonicity.*
