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

### 3.2 Approach B: Deferred Entries

**Mechanism:** Relax I-02 from "all parents must exist NOW" to "all parents must exist EVENTUALLY." Entries with missing parents go into a `deferred` set. When parents arrive (via later sync), deferred entries are promoted to the oplog.

**Changes required:**

```
src/oplog.rs:
  + deferred: HashMap<Hash, Entry>           // entries waiting for parents
  + missing_parents: HashMap<Hash, HashSet<Hash>>  // what each deferred entry needs
  + promote_deferred()                       // called after each append, checks if any can be promoted
  ~ append(): on MissingParent, insert into deferred instead of erroring
  ~ entries_since(): exclude deferred entries

src/graph.rs:
  No changes. Deferred entries are invisible to materialization.

src/python.rs:
  + get_deferred() -> list[str]              // inspect deferred entries
  + get_deferred_count() -> int              // how many are waiting

PROOF.md:
  + New invariant I-02': "I-02 holds eventually after all ancestors synced"
  + Partial convergence theorem: "peers converge within their scope"
```

**Estimated code:** ~350 lines of Rust + ~100 lines of Python tests.

**Convergence proof sketch:**
1. Within the non-deferred entry set, I-02 holds (all parents exist)
2. Materialization only applies non-deferred entries → graph is consistent
3. When a deferred entry's last missing parent arrives, it's promoted → graph grows monotonically
4. After all parents synced, the full entry set converges (reduces to Theorem 3)

**What it solves:** Graceful handling of incomplete sync. No crash on missing parents. Entries arrive in any order.
**What it doesn't solve:** Bandwidth — causal closure still pulls most entries. Storage — all entries eventually stored.
**Tradeoff:** Correctness (eventually) vs completeness (immediately).

### 3.3 Approach C: Scope-Aware Protocol

**Mechanism:** New sync mode where peers declare a `SyncScope` (set of node types, predicates). The sender filters entries AND skips causal closure for entries outside scope. The receiver knows it has an intentionally partial oplog.

**Changes required:**

```
src/sync.rs:
  + SyncScope { node_types: HashSet<String>, include_edges: bool }
  + entries_missing_scoped(oplog, offer, scope) -> SyncPayload
  ~ SyncPayload gains scope: Option<SyncScope> field

src/oplog.rs:
  + Deferred entries (from Approach B)
  + scope_heads(scope) -> Vec<Hash>          // heads within scope only

src/graph.rs:
  + apply_scoped(entry, scope) -> bool       // only materialize if in scope
  + rebuild_scoped(entries, scope)            // filtered rebuild

src/python.rs:
  + set_sync_scope(scope)                    // declare what this peer wants
  + generate_scoped_sync_offer(scope)        // offer with scope metadata

PROOF.md:
  + Scope convergence theorem: π_S(G_A) = π_S(G_B) for shared scope S
  + Cross-scope edge semantics documented
```

**Estimated code:** ~500 lines Rust + ~200 lines Python + new proof section.

**Cross-scope edges:** If an edge connects a server (in scope) to an alert (out of scope):
- Option C1: Include the edge. Source/target may not exist on receiver. Edge is dangling.
- Option C2: Exclude the edge. Receiver's graph is incomplete at the boundary.
- Option C3: Include the edge + the missing endpoint as a "stub" (node_id + type, no properties).

**What it solves:** True partial sync with explicit scope. Peers sync only what they need.
**What it doesn't solve:** Cross-scope consistency without coordination. Dynamic scope changes (what happens when you expand your scope?).
**Tradeoff:** Capability vs complexity. Full design + proof required before implementation.

---

## 4. Tensions & Tradeoffs

### Bandwidth vs Correctness

Filtering reduces bandwidth. But the Merkle-DAG's causal chain links everything. You can either:
- Keep causal closure → full bandwidth (Approach 2 today)
- Break causal closure → entries with missing parents → deferred (Approach B)
- Skip causal closure entirely → convergence proof breaks → need new proof (Approach C)

### Simplicity vs Capability

| | Separate stores (A) | Deferred entries (B) | Scope protocol (C) |
|---|---|---|---|
| Code complexity | 0 lines | ~350 lines | ~700 lines |
| Convergence risk | None | Low (deferred = eventually consistent) | Medium (new invariants) |
| Cross-domain edges | Impossible | Possible (deferred) | Possible (with stubs) |
| Bandwidth reduction | Full (separate streams) | Minimal (causal closure) | Significant |
| Storage reduction | Full (separate stores) | None | Possible (scope-pruning) |

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
- **Approach B: Deferred entries** — the minimum viable step. Accept entries with missing parents gracefully. No crash on incomplete sync. ~350 lines of Rust. Low convergence risk (deferred entries are invisible until complete).

### Future (requires formal design)
- **Approach C: Scope-aware protocol** — true partial sync with explicit scope negotiation. Requires: new SyncScope struct, scoped entries_missing, partial convergence proof, cross-scope edge semantics. Estimated effort: 700+ lines of Rust, new PROOF.md section, protocol version bump.

### Not recommended
- Attempting partial sync without deferred entries. The causal chain breaks immediately.
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
