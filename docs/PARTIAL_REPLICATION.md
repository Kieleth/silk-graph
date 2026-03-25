# Partial Replication in Silk: Research & Analysis

> Research document. No code changes. Produced on branch `research/partial-sync`.
> Date: 2026-03-24. Based on silk-graph v0.1.5 (commit `391e70c`).

---

## 1. State of the Art

### 1.1 The Problem

Full replication: all peers hold all data. Convergence is guaranteed because the CRDT merge function operates over the complete lattice. Simple, proven, and what Silk does today.

Partial replication: different peers hold different subsets. The merge function may not have all the information needed to compute the correct result. Convergence becomes conditional.

**The fundamental impossibility (Shapiro et al., OPODIS 2017):** You cannot simultaneously have genuine partial replication + causal consistency + tolerance of indefinite replica failures. If a failed replica rejoins with operations for objects you've garbage-collected, causal consistency breaks. You must choose which property to relax.

### 1.2 Academic Foundations

#### Conflict-free Partially Replicated Data Types (CPRDTs)

Guerreiro (2019) formalized the challenges:
1. Operations may require data not available locally → need preconditions for safety
2. Preconditions may interfere with convergence guarantees
3. The replicated subset varies per peer → new mechanisms needed for convergence

**Key insight:** The set of replicated objects is itself a distributed state that must be tracked.

*Reference: Guerreiro, "Partial Replication of Conflict-Free Replicated Data Types," INESC-ID Technical Report, 2019.*

#### Non-Uniform Replication

Shapiro, Baquero, and Preguiça (OPODIS 2017) proved:
- All replicas must eventually see the same causal history for objects they both replicate
- Replicas cannot simply ignore objects they don't replicate — they must track which others hold them
- Under fault tolerance, replicas must maintain operations for objects they don't replicate to ensure correctness when replicas rejoin

**Implication for Silk:** Even if peer A filters out "alert" entries, it must remember that peer B might have alerts that causally depend on A's servers. If B sends those alerts later, A must handle them.

*Reference: Shapiro, Preguiça, Baquero, "Non-Uniform Replication," OPODIS 2017, LIPIcs 95.*

#### Delta-State CRDTs

Enes, Baquero, Almeida (2018) showed that delta-state CRDTs reduce sync bandwidth by up to 94% by sending only changes since the last sync. Deltas maintain the join-semilattice properties needed for convergence.

**Relevance:** Delta sync is orthogonal to partial replication. Silk already uses delta sync (entries_missing + bloom filter). The question is whether you can apply delta sync to a *subset* of the state.

*Reference: Enes et al., "Efficient Synchronization of State-based CRDTs," arXiv:1803.02750, 2018.*

#### Merkle-CRDT Anti-Entropy

Protocol Labs (2020) showed that Merkle-DAGs enable efficient anti-entropy: compare root hashes to find divergence in O(log N), then transfer only missing branches. This is what Silk's sync protocol implements.

**Limitation:** Merkle-CRDTs don't natively support subscription filtering. The DAG is the complete history. Filtering requires a layer above the DAG.

*Reference: Sanjuán et al., "Merkle-CRDTs: Merkle-DAGs meet CRDTs," arXiv:2004.00107, 2020.*

### 1.3 Industry Implementations

| System | Pattern | How it filters | Tradeoff |
|--------|---------|---------------|----------|
| **Ditto** | Subscription-based | Client declares queries; peer mesh syncs matching docs | Continuous subscription evaluation; closed source |
| **Electric SQL** | Shapes | SQL WHERE clauses define sync subsets; server evaluates | Centralized authority (Postgres is source of truth) |
| **PowerSync** | Bucket parameters | Server-defined bucket templates with dynamic params (user_id) | Combinatorial explosion at scale; server-mediated |
| **MongoDB Realm** | Partition keys | Developer designates partition column; exact-match only | Mutually exclusive partitions; no range queries |
| **CouchDB/PouchDB** | Filtered replication | JavaScript filter functions per document | Originally a workaround; slow filter evaluation |
| **Loro** | Version vectors | Export deltas from specific version points | Doesn't reduce storage; all history retained |
| **Figma** | Server-ordered + CRDT text | Server defines structural order; CRDT for text content | Not fully distributed; requires server for ordering |

**Common pattern:** All production systems with partial sync have a **coordinator** — either a server (Electric SQL, PowerSync, Realm) or a subscription evaluator (Ditto). Purely peer-to-peer partial sync without coordination is not deployed in production by anyone.

### 1.4 Key Patterns

**Pattern 1: Subscription-Based (Ditto)**
- Client declares: "I want documents where type=server AND region=eu"
- System pushes matching updates continuously
- Pro: Fine-grained, dynamic
- Con: Subscription evaluation at every write

**Pattern 2: Partition-Key (Realm, PowerSync)**
- Data organized by partition key (user_id, org_id)
- Each replica gets one partition
- Pro: Simple, scalable, cache-friendly
- Con: No cross-partition queries; shared data is hard

**Pattern 3: Scope-Based (proposed for Silk)**
- Peers agree on a "sync scope" — a set of node types or a predicate
- Entries matching scope + their causal ancestors are synced
- Pro: Compatible with Merkle-DAG; reuses existing sync protocol
- Con: Causal closure may pull in more than intended

**Pattern 4: Separate Stores**
- Different domains in different CRDT instances
- No cross-store edges; each store is fully replicated internally
- Pro: Zero convergence risk; simple
- Con: Can't query across stores; can't have edges between stores

---

## 2. What Silk Has Today (Silk-SOTA)

### 2.1 Full Replication

Silk's sync protocol (`src/sync.rs`) guarantees that after bidirectional sync, both peers have identical entry sets. This is proven in `PROOF.md` Theorem 3.

The protocol works in three phases:
1. **Bloom filter**: fast approximation of what the remote has
2. **Force heads**: ensure DAG tips are never lost to bloom false positives (C-075)
3. **Ancestor closure**: walk up the DAG to include all causal parents of sent entries

**Invariant I-02 (Causal Completeness)**: enforced at `oplog.rs:55-62`. Every entry's parents must exist in the oplog before the entry can be appended. This is a hard guarantee — violation crashes with `MissingParent`.

### 2.2 GraphView (Approach 1 — shipped v0.1.5)

`python/silk/views.py`: A read-only filtered projection over the full graph. The oplog is unchanged. CRDT convergence is preserved because the underlying entry set is identical on all peers. Only the *view* differs.

```python
view = GraphView(store, node_types=["server"])
view.all_nodes()  # only servers
view.all_edges()  # only edges where BOTH endpoints are servers
```

**What it solves:** Query-time filtering. Dashboards, role-based views, type-specific APIs.
**What it doesn't solve:** Bandwidth, storage, mobile/IoT resource constraints.

### 2.3 Filtered Sync (Approach 2 — shipped v0.1.5)

`src/python.rs:receive_filtered_sync_offer()`: Filters the sync payload by node type, then runs causal closure to ensure the receiver gets a valid oplog.

**The causal chain problem:** In a single DAG where entries are appended sequentially, every entry is a causal descendant of genesis via the `next` field. Filtering by type but keeping causal closure pulls in ALL ancestors — which includes entries of every type.

Example:
```
genesis → server-1 → service-1 → server-2 → alert-1 → server-3
                                                        ↑
Filter: types=["server"]
Causal closure: server-3 → alert-1 → server-2 → service-1 → server-1 → genesis
Result: EVERYTHING included (causal chain links all entries)
```

**When it works:** Only when filtered types have NO causal links to excluded types — which means they were written in separate batches with no interleaving. In practice, this is rare.

### 2.4 Where the Current Architecture Breaks

| Component | Assumption | Breaks when |
|-----------|-----------|-------------|
| `oplog.append()` | All parents exist (I-02) | Filtered entries have missing parents |
| `entries_since()` | Returns ALL entries | Filtered oplog has gaps |
| `topo_sort()` | All entries sortable | Missing entries break sort |
| `merge_entries()` | Retry loop resolves missing parents | Parents are intentionally missing (filtered) |
| `PROOF.md` Theorem 3 | Identical entry sets | Peers have intentionally different sets |
| `entries_missing()` Phase 2 | Ancestor closure is complete | Filtered entries break closure |

---

## 3. Gap Analysis: What Would It Take

### 3.1 Approach A: Separate Stores (Shipped)

**Mechanism:** Different domains live in different `GraphStore` instances. Each store is fully replicated internally. No cross-store edges.

```python
server_store = GraphStore("servers", server_ontology)
alert_store = GraphStore("alerts", alert_ontology)
# Each syncs independently with its own peers
```

**What it solves:** Full isolation. Each store has convergence. No causal chain problem.
**What it doesn't solve:** Cross-domain queries. Edges between servers and alerts. Unified graph view.
**Tradeoff:** Simplicity vs expressiveness.

**Status:** Already possible with Silk today. No code changes needed.

### 3.2 Approach B: Deferred Entries — REJECTED

**Mechanism:** Relax I-02 from "all parents must exist NOW" to "all parents must exist EVENTUALLY." Entries with missing parents go into a `deferred` set. When parents arrive (via later sync), deferred entries are promoted to the oplog.

**Why it doesn't work for Silk:**

The deferred mechanism solves a mechanical problem (don't crash on `MissingParent`) but creates a semantic one: the query model becomes unpredictable.

In Silk's single-DAG oplog, entries are linked by the `next` field (causal ordering). If `server-3` was appended after `alert-1`, then `server-3`'s DAG parent is `alert-1` — regardless of their graph-level relationship. With filtered sync excluding alerts:

1. `server-3` arrives but its DAG parent `alert-1` is missing → deferred
2. `alert-1` may **never** arrive (it's out of scope) → `server-3` stays deferred forever
3. Or `alert-1` eventually arrives → `server-3` promotes, but its `AddEdge` entries might still be deferred → server-3 appears as a disconnected island

**Two failure modes:**
- **Entries you want stay invisible** — trapped in deferred because their DAG parents are out of scope
- **Entries that promote appear disconnected** — edges lag behind nodes, graph has orphan islands

Both defeat the query model. `store.all_nodes()` cannot be trusted — some in-scope nodes are hidden in deferred, and promoted nodes may lack edges. This is worse than full replication (predictable, complete) or separate stores (predictable, isolated).

**Conclusion:** B is useful as internal plumbing (a `MissingParent` that doesn't crash) but useless as a partial replication strategy. It doesn't reduce bandwidth, doesn't reduce storage, and makes the graph unreliable. If B is ever implemented, it should be as a building block inside C — never exposed as a user-facing sync mode.

### 3.3 Approach C: Scope-Aware Protocol

**Mechanism:** New sync mode where peers declare a `SyncScope` (set of node types, predicates). The sender filters entries AND skips causal closure for entries outside scope. The receiver knows it has an intentionally partial oplog.

#### Sub-Problem 1: What is a scope?

The simplest version: a set of node types.

```
SyncScope { node_types: {"server", "rack"}, include_cross_edges: true }
```

More expressive options, each adding complexity:
- **Predicate-based**: `region == "eu"` (like Ditto subscriptions) — requires evaluating predicates at every write
- **Partition-key**: `org_id == "acme"` (like Realm) — simple but inflexible, mutually exclusive partitions
- **Subgraph**: "everything reachable from node X within 3 hops" — graph-dependent, expensive to compute

Node-type filtering is the minimum viable scope — it maps directly to Silk's ontology and can be evaluated without graph traversal.

#### Sub-Problem 2: What happens to the DAG?

This is the hard part. Today, every entry's `next` field points to previous entries — forming a single causal chain. Filtering by scope breaks this chain. Two options:

**C-α: Scope-local DAG (rewrite causal chain per scope)**
- When generating a scoped sync payload, rewrite `next` fields to point only to the previous in-scope entry
- The receiver gets a valid sub-DAG where every entry's parents exist
- Pro: I-02 holds within scope. Clean sub-DAG.
- Con: **Rewriting `next` changes the entry hash** → entries are no longer content-addressed across scopes. A "server" entry has different hashes on different peers depending on their scope. **This breaks deduplication, Merkle verification, and the convergence proof.**
- **Verdict: Rejected.** Content addressing is foundational to Silk.

**C-β: Scope envelope (metadata layer above the DAG)**
- Don't touch the DAG entries at all — their hashes remain canonical
- Add a `ScopeEnvelope` that wraps the sync payload: declares scope, lists included entry hashes, and provides a scope-local Merkle root
- Receiver stores entries with their original hashes but tracks which scope delivered them
- I-02 is relaxed for scoped sync: entries within the envelope are not required to have all DAG parents — only parents that are also within scope
- Pro: Entry hashes unchanged. Content addressing works. Deduplication works across scopes.
- Con: Needs a new invariant (I-02-scoped) and a modified `append()` path that tolerates intentional gaps
- **Verdict: Viable.** This is the path forward.

The key insight: B's deferred mechanism could serve as internal plumbing here — entries with missing parents don't crash, they're accepted as "scoped entries" with known-absent ancestors. But the user never sees "deferred" — they see a complete graph within their declared scope.

#### Sub-Problem 3: Cross-scope edges

If an edge connects `server-1` (in scope) to `alert-5` (out of scope):

| Option | Behavior | Query impact | Complexity |
|--------|----------|--------------|------------|
| **C3-a: Drop** | Edge excluded from sync | Graph clean but incomplete at boundaries. `server-1.edges()` is missing connections. | Low |
| **C3-b: Dangle** | Edge included, target doesn't exist | Queries must handle `None` targets. Every graph traversal needs null checks. | Medium |
| **C3-c: Stub** | Edge + lightweight placeholder (node_id + type, no properties) | Queries see connection exists, can't inspect remote node. Clear boundary marker. | Medium |

C3-c (stubs) is the most informative: the query model remains predictable ("I can see this server connects to an alert, but I don't have the alert's details"). The stub acts as a boundary marker — the application knows exactly where its scope ends.

#### Sub-Problem 4: Scope changes

What happens when a peer expands its scope (e.g., adds "alert" to its scope)?

- **Backfill needed**: the peer must sync all historical alert entries it never received
- This is a regular sync with a wider scope — the bloom filter will show all alert entries as "missing"
- **Shrinking scope**: simpler — stop syncing entries of that type. Existing entries can be pruned or kept.

#### Sub-Problem 5: Convergence proof

New theorem required:

> **Theorem 3-S (Scoped Convergence):** For peers A and B with scopes S_A and S_B, after bidirectional scoped sync, the materialized graphs restricted to their shared scope intersection converge: π_{S_A ∩ S_B}(G_A) = π_{S_A ∩ S_B}(G_B).

Proof sketch:
1. Within scope S, all entries of types in S are exchanged (bloom filter + force heads, same as full sync)
2. Cross-scope edges handled by chosen policy (drop/dangle/stub) — deterministic
3. Entry hashes unchanged (C-β) → deduplication and idempotent merge still hold
4. LWW conflict resolution operates on the same entry set within S → deterministic materialization
5. Reduces to Theorem 3 when S_A = S_B = all types

**Open question:** Does scoped sync preserve the causal ordering guarantees that the HLC provides? If peer A has alert entries that causally depend on server entries, and peer B only syncs servers, B's causal history for those servers is incomplete. This may not matter for materialization (LWW doesn't need full causal history, just clocks) but needs formal verification.

#### Changes required

```
src/sync.rs:
  + SyncScope { node_types: HashSet<String>, include_cross_edges: bool }
  + ScopeEnvelope { scope: SyncScope, entries: Vec<Hash>, scope_root: Hash }
  + entries_missing_scoped(oplog, offer, scope) -> ScopedSyncPayload
  ~ SyncPayload: add scope: Option<SyncScope> field

src/oplog.rs:
  + append_scoped(): accepts entries with known-absent parents (within scope envelope)
  + scope_heads(scope) -> Vec<Hash>          // heads within scope only

src/graph.rs:
  + apply_scoped(entry, scope) -> bool       // only materialize if in scope
  + StubNode { id, node_type }               // lightweight boundary marker
  + rebuild_scoped(entries, scope)            // filtered rebuild with stubs

src/python.rs:
  + set_sync_scope(scope)                    // declare what this peer wants
  + generate_scoped_sync_offer(scope)        // offer with scope metadata
  + get_stub_nodes() -> list                 // inspect boundary markers

PROOF.md:
  + Theorem 3-S: Scoped convergence
  + Invariant I-02-S: Scoped causal completeness
  + Cross-scope edge semantics (chosen policy)
```

**Estimated code:** ~700 lines Rust + ~200 lines Python + new PROOF.md section + protocol version bump.

**What it solves:** True partial sync with explicit scope. Peers sync only what they need. Query model remains predictable within scope.
**What it doesn't solve:** Cross-scope consistency without coordination. The HLC causal ordering question (needs formal verification).
**Tradeoff:** Capability vs complexity. Requires formal design + proof before implementation.

#### Open design decisions (not yet taken)

1. **Scope definition** — node types only, or more expressive? (affects evaluation cost)
2. **Cross-scope edges** — C3-a (drop), C3-b (dangle), or C3-c (stub)?
3. **Scope negotiation** — how do peers communicate their scopes? (in SyncOffer? separate handshake?)
4. **Scope storage** — does the receiver persist its scope declaration? (needed for scope-change backfill)

---

## 4. Tensions & Tradeoffs

### Bandwidth vs Correctness

Filtering reduces bandwidth. But the Merkle-DAG's causal chain links everything. You can either:
- Keep causal closure → full bandwidth (current filtered sync)
- Break causal closure → unpredictable query model (Approach B — rejected)
- Replace the causal model within a declared scope → new proof required (Approach C)

### Simplicity vs Capability

| | Separate stores (A) | Scope protocol (C) |
|---|---|---|
| Code complexity | 0 lines | ~700 lines |
| Convergence risk | None | Medium (new invariants) |
| Cross-domain edges | Impossible | Possible (with stubs) |
| Bandwidth reduction | Full (separate streams) | Significant |
| Storage reduction | Full (separate stores) | Possible (scope-pruning) |
| Query model | Predictable (isolated) | Predictable (within scope) |

### Offline vs Coordination

True partial sync without a coordinator is an unsolved research problem (Shapiro et al. 2017). Every production system with partial sync has some form of coordination:
- Ditto: mesh + subscription evaluator
- Electric SQL: Postgres as authority
- PowerSync: server-defined buckets

For Silk's peer-to-peer model, the coordinator would need to be the peers themselves — negotiating scope, tracking what each peer has, handling scope changes. This is research-grade complexity.

---

## 5. Recommendation

### Now (v0.1.5 — shipped)
- **GraphView** for query-time filtering ✓
- **Filtered sync** for best-effort bandwidth reduction ✓
- **Separate stores** for domain isolation ✓

### Next (v0.2 candidate)
- **Approach C: Scope-aware protocol** — true partial sync with explicit scope negotiation. Requires: new SyncScope struct, scoped entries_missing, partial convergence proof, cross-scope edge semantics. Estimated effort: 700+ lines of Rust, new PROOF.md section, protocol version bump.

### Rejected
- **Approach B: Deferred entries** — solves a mechanical problem (no crash on missing parents) but breaks the query model. Nodes trapped in deferred or appearing as disconnected islands. Useless as a standalone strategy. May be reused as internal plumbing inside C.

### Not recommended
- Attempting scope-aware sync without a formal proof. Convergence bugs are silent and catastrophic.
- Comparing Silk to Ditto/Electric SQL on partial sync. They have centralized authority; Silk is peer-to-peer. Different problem space.

---

## References

### Academic
- Guerreiro, H. (2019). "Partial Replication of Conflict-Free Replicated Data Types." INESC-ID Technical Report.
- Shapiro, M., Preguiça, N., Baquero, C. (2017). "Non-Uniform Replication." OPODIS 2017, LIPIcs Vol. 95.
- Enes, V., Baquero, C., Almeida, P. S. (2018). "Efficient Synchronization of State-based CRDTs." arXiv:1803.02750.
- Sanjuán, H. et al. (2020). "Merkle-CRDTs: Merkle-DAGs meet CRDTs." arXiv:2004.00107.
- Shapiro, M. et al. (2011). "Conflict-free Replicated Data Types." SSS 2011, LNCS 6976.
- Almeida, P. S., Shoker, A., Baquero, C. (2018). "Delta State Replicated Data Types." J. Parallel and Distributed Computing, Vol. 111.
- Balegas, V. et al. (2015). "Putting Consistency Back into Eventual Consistency." EuroSys 2015.
- Kleppmann, M. (2020). "Bloom filter hash graph sync." Blog post + technical report.

### Industry
- Ditto. "Syncing Data." https://docs.ditto.live/key-concepts/syncing-data
- Electric SQL. "Shapes." https://electric-sql.com/docs/guides/shapes
- PowerSync. "Sync Rules From First Principles." https://www.powersync.com/blog/sync-rules-from-first-principles
- MongoDB. "Realm Partitioning Strategies." https://www.mongodb.com/developer/products/realm/realm-partitioning-strategies/
- CouchDB. "Filtered Replication." https://docs.couchdb.org/en/stable/replication/intro.html

### Silk Internal
- `src/oplog.rs:55-62` — I-02 enforcement (MissingParent error)
- `src/sync.rs:203-247` — Three-phase sync protocol
- `src/python.rs:630-718` — receive_filtered_sync_offer (current filtered sync)
- `python/silk/views.py` — GraphView (query-time projection)
- `PROOF.md` — Convergence theorems and invariants
