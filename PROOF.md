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

### I-06: Quarantine Determinism

`G.quarantined` is grow-only within a single materialization pass. On `rebuild()` (triggered by `ExtendOntology` or `Checkpoint` during sync), the set is cleared and all entries are re-evaluated against the evolved ontology. Two peers with identical oplogs produce identical quarantine sets after rebuild — the decision is deterministic (see Section 4).

*Reference*: `src/graph.rs:69-74` (comment), `src/graph.rs:209` (`rebuild()` clears quarantine)

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

**Quarantine lifecycle**: The quarantined set is grow-only within a single materialization pass. When `rebuild()` is called (triggered by ExtendOntology or Checkpoint entries during sync), the set is cleared and all entries are re-evaluated against the current ontology. This means a previously-quarantined entry can be un-quarantined if the ontology has evolved to accept it — for example, when an ExtendOntology entry adds the type that was missing. This is correct: the quarantine decision depends on the ontology at materialization time, and `rebuild()` ensures the ontology and quarantine set are always consistent.

**Note**: Two peers with identical OpLogs produce identical quarantined sets — topological ordering is deterministic (Section 5, Case 2), and `rebuild()` replays all entries in that order against the same evolved ontology. Quarantined sets can only differ if peers have genuinely different entry sets (e.g., mid-sync, before full convergence). After bidirectional sync, quarantine sets converge along with the OpLog.

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

**Claim**: A compacted OpLog converges with uncompacted peers, provided the safety precondition holds.

### 6.1 Safety Precondition

A checkpoint at time T is safe iff ALL known peers have synced past T. Formally: for every peer P in the known set, every entry e in the compacting peer's oplog with `clock(e) ≤ T` has been received and processed by P.

**This precondition is NOT enforced by Silk.** It is the caller's responsibility. Compacting without full peer sync can cause:
- **Zombie resurrection** (tombstone clock loss): if peer P holds a concurrent add for a deleted entity, and the compacting peer discards the tombstone, the entity reappears after sync. See EXP-02 Scenario 2.
- **Causal information loss**: if peer P holds operations concurrent with pre-compaction entries, those operations may conflict incorrectly with the checkpoint's clocks.

### 6.2 Checkpoint Construction

The checkpoint produces synthetic ops that replay into an identical MaterializedGraph:

1. **DefineOntology** — the fully-merged ontology (all extensions applied).
2. **AddNode** per live node — with `last_add_clock` (for add-wins semantics) and empty properties.
3. **UpdateProperty** per property per node — with the property's individual clock from `property_clocks`.
4. **AddEdge** per live edge — with `last_add_clock` and empty properties.
5. **UpdateProperty** per property per edge — with the property's individual clock.

Tombstoned entities are excluded (dead nodes and edges are not in the checkpoint).

**Why per-property clocks matter (EXP-02):** If a node has `status@clock1` and `name@clock5`, and a concurrent peer writes `status@clock3`, the correct LWW result is `status=peer_value` (clock3 > clock1). If the checkpoint stores a single entity-level clock (clock5), the concurrent write at clock3 loses — this is a correctness bug. Per-property UpdateProperty ops with individual clocks prevent this.

*Reference*: `src/python.rs:build_checkpoint_ops()`, EXP-02 in [EXPERIMENTS.md](EXPERIMENTS.md).

### 6.3 Post-Compaction OpLog

After compaction, the OpLog contains exactly one entry (the checkpoint, with `next=[]`). New writes have the checkpoint as their parent. The DAG structure is preserved going forward.

### 6.4 Sync with Uncompacted Peer

When an uncompacted peer syncs with a compacted peer:
- Delta sync may fail (old parent hashes missing in the compacted oplog). Fallback to snapshot bootstrap.
- Snapshot from checkpoint replays the synthetic ops, producing the same graph as the full history (by construction, claim 6.2).
- After snapshot bootstrap + delta sync of post-compaction entries, both peers converge.

### 6.5 Two Compacted Peers

Two compacted peers with checkpoints at different times still converge, because:
- Both checkpoints capture the same logical state (safety precondition ensures all entries were synced before compaction).
- Post-checkpoint entries are exchanged via the normal sync protocol.
- Materialization is deterministic (Theorem 1).

### 6.6 What Compaction Does NOT Preserve

- **Tombstone clocks** — deleted entities are excluded. If the safety precondition is violated (a peer holds a concurrent add), the tombstone cannot suppress it. This is a documented limitation, not a bug.
- **Operation history** — the sequence of mutations is collapsed into a state snapshot. Time-travel queries (`as_of`) cannot look before the compaction point.
- **Quarantine metadata** — quarantined entries are excluded. The compacted peer forgets that certain entries were invalid.

Therefore, under the safety precondition, compaction preserves convergence. ∎

---

## 8. What This Proof Does NOT Cover

- **Liveness**: This proof shows convergence (safety), not that sync will eventually complete (liveness). Liveness depends on network connectivity and the gossip protocol (R-05).

- **Byzantine fault tolerance**: A malicious peer can inject valid-looking entries with spoofed clocks. Signatures (D-027) authenticate authors but don't prevent a compromised key from issuing bad data. Full BFT requires trust policies and quorum mechanisms beyond Silk's scope.

- **Performance bounds**: This proof shows correctness, not efficiency. Sync may transfer redundant entries (Bloom false positives); ancestor closure may be slow on adversarial DAGs. These are performance issues, not correctness issues.

- **Machine verification**: This proof is semi-formal — structured reasoning with code references. Two critical slices have been machine-checked via TLA+ and the TLC model checker:
  - **I-02 (Causal Completeness):** verified for a single oplog with 5 entry hashes (9,569 distinct states, zero violations). See `formal/OpLog.tla`.
  - **Theorem 3 (Sync Convergence):** verified for two peers with 4 entry hashes across all interleavings of writes and syncs (99,494 distinct states, zero violations). See `formal/SilkSync.tla`.

  These are bounded model checks (all states explored up to the hash limit), not unbounded proofs. For full deductive verification, Isabelle/HOL formalization is the next step.

---

*Proof structure follows Shapiro et al. (2011) Section 3.2: state-based CRDT convergence via join-semilattice properties. The OpLog entry set forms a join-semilattice under set union. Materialization is a monotonic function from the lattice to the graph domain. Quarantine and ontology evolution preserve monotonicity.*

---

## Appendix A: Provenance Observation

This appendix formalizes the algebras already in use in Silk's materialized graph, then states two additional theorems that justify the read-only `store.entries_affecting(id)` API.

### A.1 Feynman toy example

Two peers, identical ontology. Walk through the clocks.

1. Peer A at clock 5 appends `AddNode("node-1", name="foo")`. A's OpLog now has `[genesis, add_node_1]`.
2. Peer B at clock 10 appends `RemoveNode("node-1")`. B's OpLog has `[genesis, remove_node_1]`.
3. Peer A at clock 8 appends `AddEdge("e1", source="node-1", target="node-2")`. A's OpLog: `[genesis, add_node_1, add_edge_1]`.
4. Bidirectional sync. Both OpLogs converge to `{genesis, add_node_1, remove_node_1, add_edge_1}`.

What does the materialized graph show?

- `node-1.last_add_clock = 5`, `remove_clock = 10`. Add-wins requires `remove_clock > last_add_clock` to tombstone; 10 > 5 is true, so `node-1` is tombstoned.
- `e1` stays in the graph but `is_node_live(source)` filters it out of queries, so `get_edge("e1")` returns `None` on both peers.

What does `store.entries_affecting("node-1")` return on either peer? The three entries that mention `node-1` by id or by edge endpoint: `add_node_1`, `remove_node_1`, `add_edge_1`, in topological order. A consumer reading this result can answer *why* the node is gone by inspecting the clocks: the tombstone's clock 10 beats the add's clock 5, and add-wins requires strict inequality (satisfied).

The formalism below names what just happened.

### A.2 The two algebras: clock and existence semilattices

Following Shapiro, Preguiça, Baquero, Zawirski (2011) [CRDT], a CRDT converges under arbitrary delivery order iff its state lives in a join-semilattice: a set with a single idempotent commutative associative join operation ⊔. Merging two states is `s₁ ⊔ s₂`.

Silk composes two independent join-semilattices in the materialized graph.

**Clock semilattice.** The set is `HybridClock` values. The join is `max` under the lexicographic order `(physical_ms, logical, reverse(author_id))` implemented by `clock_wins` in `src/graph.rs`. Identity: the genesis clock. The operation is idempotent (`a ⊔ a = a`), commutative (`a ⊔ b = b ⊔ a`), and associative (`(a ⊔ b) ⊔ c = a ⊔ (b ⊔ c)`) by properties of `max` on a totally ordered tuple. Used for `property_clocks`, `last_clock`, and `last_add_clock`.

**Existence semilattice.** For each id, the state is one of `{NeverExisted, Tombstoned, Live}`. The join is not a linear order: under add-wins, `Live ⊔ Tombstoned = Live` when `last_add_clock ≥ remove_clock`, else `Tombstoned`. Identity: `NeverExisted`. Idempotence, commutativity, and associativity follow because the decision is driven by the clock semilattice, which is itself idempotent/commutative/associative. Used for the `tombstoned: bool` flag in `src/graph.rs:27,43`.

These two algebras are independent. Existence resolution does not depend on which property clocks are in flight; per-property resolution matters only inside `Live`.

### Theorem 4: Composition of the Clock and Existence Semilattices

**Statement**: the product `Clock × Existence` under pairwise join is itself a join-semilattice. Merging two materialized graphs equals pairwise-joining every `(id, clock, existence)` triple.

**Proof sketch**: the direct product of two join-semilattices is a join-semilattice — a standard algebraic result. Silk's `apply_*` functions implement the product join exactly: `apply_remove_node` consults the Clock semilattice (`clock_wins(clock, &node.last_add_clock)`) to decide the Existence join verdict (`tombstoned = true`). Independence of the two projections ensures the product structure is well-defined. Convergence follows from Theorem 3 applied to the product state. ∎

### Theorem 5: Provenance Observation

**Statement**: given an OpLog `L` and an id `i`, let `affect(L, i)` be the set of entries in `L` whose `GraphOp` payload references `i` (as `node_id`, `edge_id`, `source_id`, `target_id`, `entity_id`, or recursively inside a `Checkpoint`'s embedded ops). Then `store.entries_affecting(i)` returns exactly the topologically ordered sequence of entries in `affect(L, i)`, deterministic over `L` alone.

**Proof sketch**: `entries_affecting` (`src/provenance.rs`) scans every entry in the OpLog, applies `payload_mentions_id` to the payload, and passes the matching hash set through `OpLog::topo_sort` (`src/oplog.rs:263`). Topological sort is deterministic on a fixed DAG (proved in Theorem 1). No state outside the OpLog is consulted. Two peers with byte-identical OpLogs after sync (Theorem 3) therefore produce byte-identical results. ∎

**Corollary (CRDT safety)**: any function built on top of `entries_affecting` inherits convergence without needing to handle sync itself. A consumer that computes a typed provenance view (e.g. "winning entry per property" or "all contributors including quarantined attempts") from the returned `Vec<&Entry>` is automatically CRDT-safe provided it is a pure function of the input sequence.

### A.3 When semirings would become relevant

Silk today resolves conflicts point-wise via a single operation. That is join-semilattice territory. Green, Karvounarakis, Tannen (2007) [Semirings] use two-operation algebras (⊕ for alternatives, ⊗ for combinations) to track how provenance annotations compose as they flow through relational operators: join, projection, union. Semirings are the right algebra for that problem; semilattices are not.

If Silk ever gains query-time provenance propagation — "for this graph traversal result, which input entries contributed, through which derivation path?" — that is when semirings earn their keep. Each flavor of provenance has its own semiring:

- Lineage (which inputs survived): set-union semiring.
- Why-provenance (which inputs justified it): set-of-sets semiring.
- How-provenance (full derivation tree): free polynomial semiring.

`entries_affecting` is a *static* per-id extraction, not a compositional query. It sits inside the semilattice regime. This subsection marks the boundary: today's algebra is a semilattice; query-time provenance would require the semiring upgrade, at which point Green et al. becomes the primary reference.

### A.4 Related work

**Buneman, Khanna, Tan (2001), "Why and Where: A Characterization of Data Provenance."** Distinguishes *why-provenance* (the set of input rows justifying an output) from *where-provenance* (the specific source cell). Grounds the design choice in Task B: `entries_affecting` returns a set; callers derive where-provenance by filtering to the clock winner and why-provenance by retaining all contributors.

**Shapiro, Preguiça, Baquero, Zawirski (2011), "Conflict-Free Replicated Data Types."** The join-semilattice vocabulary used throughout this appendix. Primary theoretical reference for Theorem 4's composition argument.

**Green, Karvounarakis, Tannen (2007), "Provenance Semirings."** Cited in §A.3 as forward-looking only. Not applicable to Silk's current algebra; reserved for the moment we add query-time provenance propagation.

**Helland (2015), "Immutability Changes Everything."** Deletion in append-only systems is always a new entry, never physical erasure. Silk already embodies this: `RemoveNode` is an Entry, tombstones are computed views. `entries_affecting` is the "ask the log" surface that makes deletion provenance first-class.
