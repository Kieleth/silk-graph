# Silk Roadmap: Eight Problems, In Order

> Imagine you have a notebook where you write down everything that happens.
>
> Every time something happens — "the cat sat on the mat," "the dog ate the bone" — you write it down, and you stamp it with the time. You never erase anything. You never cross anything out. You just keep adding pages.
>
> Now imagine your friend has the same notebook. You both write things down separately, and sometimes you meet up and copy each other's pages. After you trade pages, you both have the same story.
>
> That's Silk. The notebook is the oplog. The pages are entries. Meeting up is sync.

## Dependency Graph

```
Layer 0 (no deps, parallel-safe)
  ├── R-01: Hybrid Logical Clocks
  └── R-02: Sync Quarantine

Layer 1 (depends on Layer 0)
  └── R-03: Monotonic Ontology Evolution ← R-01 + R-02

Layer 2 (depends on Layer 1)
  ├── R-04: Formal Convergence Statement ← R-01 + R-02 + R-03
  ├── R-05: Gossip Peer Selection ← R-01 + R-02
  └── R-06: Time-Travel Queries ← R-01

Layer 3 (depends on Layer 2)
  ├── R-07: Query Builder ← R-06
  └── R-08: Epoch Compaction ← R-01 + R-02 + R-03 + R-04
```

Each one makes the next one possible. None can be skipped without breaking something downstream.

---

## R-01: The Clock Is Lying ✓

> **Status: COMPLETE** — Implemented in commit c5bc05c. HybridClock replaces LamportClock in `src/clock.rs`.

Right now, Silk's clock is like counting on your fingers. Every time you write something, you say "this is thing number 47." Your friend says "this is thing number 52." When you disagree about the cat's color, whoever has the bigger number wins.

But the number doesn't mean anything real. Your friend might count faster than you. That doesn't mean they're more right.

A better clock is like wearing a watch AND counting on your fingers. You look at your watch first — "it's 3:15pm" — and then if two things happen at 3:15pm, you count: first one, second one. Now when there's a disagreement, the one that happened later *in real time* wins. That actually makes sense.

That's what a Hybrid Logical Clock does. Watch first, fingers second.

### Research

- Kulkarni, Demirbas, Madeppa, Avva & Leone (2014) — *Logical Physical Clocks and Consistent Snapshots in Globally Distributed Databases*
- Used in production by CockroachDB for MVCC timestamps

### What Changes

Replace `LamportClock { id, time }` with `HybridClock { id, physical_ms, logical }`.

On local event: `physical = max(old_physical, wall_clock)`. If physical didn't change, increment logical. Otherwise reset logical to 0.

On merge: `physical = max(local, remote, wall_clock)`. Logical follows the same rule — increment if physical tied, reset if physical advanced.

LWW tiebreaker becomes: compare physical, then logical, then instance ID. The write that happened later in real time wins.

### Why First

This changes how entries are hashed. Every hash in the system changes. Breaking change. Do it now while user base is near-zero. Every subsequent feature builds on the clock primitive.

### Depends On

Nothing.

### Unblocks

R-03, R-04, R-05, R-06, R-08 — everything.

---

## R-02: The Bouncer Left the Back Door Open ✓

> **Status: COMPLETE** — Quarantine implemented in `graph.rs::apply()`. Invalid entries accepted into oplog, hidden from materialized graph. Python API: `get_quarantined()`.

Silk has rules about what's allowed. "Cats must have a name. Dogs must have an owner." When *you* write in your notebook, Silk checks the rules. Good.

But when your friend sends you *their* pages, Silk just staples them in. No checking. Your friend could have written "a fish named @#$%" and Silk would accept it.

The fix: check the rules when stapling in pages too. If a page breaks the rules, put it in a separate pile called "quarantine." It's still in the notebook (so everyone agrees the page exists), but it doesn't count when you read the story.

### Research

- Balegas, Duarte, Ferreira, Rodrigues, Preguiça & Shapiro (2015) — *Putting Consistency Back into Eventual Consistency*

### Why Quarantine, Not Reject

Rejecting entries breaks the CRDT. If peer A rejects an entry that peer B accepted, their oplogs diverge and convergence is violated. The oplog must contain the same entries everywhere. The *view* can differ (quarantine is local policy), but the *log* cannot.

The quarantine set is itself a grow-only set — a valid CRDT. Entries enter quarantine. They never leave. Monotonic. Safe.

### What Changes

`MaterializedGraph` gains a `quarantined: HashSet<Hash>`. During `apply()`, before applying an entry, validate its payload against the ontology. If validation fails, add the entry's hash to quarantined and skip materialization. The entry stays in the oplog for convergence. It's just invisible in the graph.

Edge validation has a subtlety: when an `AddEdge` arrives via sync, the source/target nodes might not be materialized yet (out-of-order within the same batch). Only quarantine if source/target types are known and violate constraints. If unknown, apply optimistically.

### Depends On

Nothing. Parallel with R-01.

### Unblocks

R-03, R-04, R-08.

---

## R-03: The Rules Can Never Change ✓

> **Status: COMPLETE** — `GraphOp::ExtendOntology` variant implemented. Python API: `extend_ontology(json)`. Monotonicity validated (add-only). Concurrent extensions merge by union; conflicts quarantined (R-02).

Right now, Silk's rules are set in stone at the very beginning. "The world has cats and dogs." That's it. Forever. If you discover birds exist, you need a whole new notebook.

That's like writing the dictionary once and never adding new words. Languages don't work that way. Knowledge doesn't work that way.

The fix: you can add new pages that say "birds also exist now." You can add new words. You can say "actually, a name isn't required for cats anymore." What you CAN'T do is take words away or change what they mean. You can only grow the dictionary, never shrink it.

Growing only is important because if you and your friend both add different words independently, you just combine both — you get a bigger dictionary that includes everything either of you added. No conflict.

### Research

- Baquero & Preguiça (2012) — Add-only sets as CRDTs (join = union, monotonic)
- Protobuf's forward compatibility rules — add fields freely, never remove or renumber
- OWL 2 monotonicity (Grau et al. 2008) — adding axioms only increases valid inferences

### The Rules of Evolution

1. **Add new node types** — always safe
2. **Add new edge types** — always safe
3. **Add new optional properties** to existing types — safe
4. **Add new subtypes** — safe
5. **Relax required to optional** — safe (existing data still valid)
6. **Cannot remove types** — would invalidate existing data
7. **Cannot remove properties** — same
8. **Cannot tighten optional to required** — existing data might lack it
9. **Cannot change property types** — existing data might have wrong type

### What Changes

New `GraphOp::ExtendOntology` variant. Contains added types, added properties, relaxed constraints. Validated for monotonicity (only additive changes pass). Applied to the in-memory ontology during materialization.

Concurrent extensions from different peers merge by union. Two peers add different types independently — after sync, both types exist. Same type name with different definitions — quarantined (R-02 handles this).

### Why It Needs R-02

If the ontology can evolve, sync validation becomes more complex. Entries must be validated against the ontology *as it existed when the entry was created*, not the current ontology. The quarantine model gives a clean answer: validate against the current ontology, quarantine if invalid. If a future `ExtendOntology` entry makes it valid, the application can re-evaluate (or simply accept that quarantine is permanent — simpler, still safe).

### Depends On

R-01 (HLC timestamps on schema changes), R-02 (quarantine handles conflicts).

### Unblocks

R-04, R-08.

---

## R-04: "Trust Me" Isn't a Proof ✓

> **Status: COMPLETE** — `PROOF.md` documents three convergence theorems, six invariants, and addenda for quarantine and ontology evolution. Semi-formal, code-referenced, structured for mechanical verification.

Silk says "if you and your friend trade pages, you'll end up with the same story. It's math." But there's no actual math written down. There are tests that check it thousands of times. But checking isn't proving. "I flipped the coin 10,000 times and it never landed on its edge" doesn't prove it can't.

The fix: write down exactly WHY it works, in three steps.

**One** — if you both have the same pages, you get the same story. (Because there's only one way to put pages in order, and the story follows from the order.)

**Two** — when you trade pages, you end up with the combined set. (Because adding a page you already have does nothing, and adding a new page just grows the set.)

**Three** — combining sets works the same regardless of order. (A ∪ B = B ∪ A. Always. That's just how sets work.)

That's the whole proof. Once it's written down, you can trust it. And more importantly, you can check whether future changes (like compaction) would break it.

### Research

- Shapiro, Preguiça, Baquero & Zawirski (2011) — *Conflict-free Replicated Data Types* (the original CRDT paper, formal framework)
- Kleppmann, Gomes, Mulligan & Beresford (2017) — Formal verification of Automerge's OpSet in Isabelle/HOL
- Hellerstein & Alvaro (2020) — CALM theorem: monotonic programs converge without coordination

### Deliverable

A `PROOF.md` document. Not machine-verified (that's a separate, larger effort), but precise enough to be mechanically verifiable later.

Structure:
1. **Definitions** — Entry, OpLog, MaterializedGraph, topological order, clock_wins, apply semantics
2. **Invariants** — hash uniqueness, causal completeness, clock monotonicity
3. **Three theorems** with proof sketches referencing code
4. **Quarantine addendum** — quarantine doesn't affect oplog convergence
5. **Ontology evolution addendum** — union of type sets is commutative

### Why It Needs R-01, R-02, R-03

Prove the final system, not an intermediate one. If you prove convergence with Lamport clocks and then switch to HLC, the proof is invalid. Stabilize the model first, then prove it.

### Depends On

R-01, R-02, R-03.

### Unblocks

R-08 (compaction needs to know what invariants are safe to preserve).

---

## R-05: You Don't Need to Talk to Everyone ✓

> **Status: COMPLETE** — `gossip.rs` module with `PeerRegistry`. Python API: `register_peer()`, `select_sync_targets()`, `record_sync()`. Fan-out: `ceil(ln(N) + 1)` per round.

Right now, every time you want to sync, you meet up with every single friend. If you have 10 friends, that's 10 meetings. If you have 100 friends, that's 100 meetings. If you have 1,000... you get it.

But think about gossip. You don't need to tell everyone the news directly. You tell 3 friends. They each tell 3 friends. After a few rounds, everyone knows.

The fix: each round, pick a few friends at random (roughly the logarithm of the total — so 7 out of 1,000) and sync with them. The news spreads like a rumor. After a few rounds, everyone has everything.

### Research

- Demers, Greene, Hauser, Irish, Larson, Shenker, Sturgis, Swinehart & Terry (1987) — *Epidemic Algorithms for Replicated Database Maintenance*
- Das, Gupta & Motivala (2002) — SWIM protocol (Consul/Serf)
- Leitão, Pereira & Rodrigues (2007) — Plumtree (hybrid push/lazy gossip)

### What Changes

New module: `gossip.rs`. Contains `PeerRegistry` and `PeerSelection` strategy. Each tick, instead of syncing with all N-1 peers, call `select_sync_targets()` which returns `ceil(ln(N) + 1)` randomly chosen peers.

The sync protocol itself doesn't change (it's already transport-agnostic per D-016). This is purely about *who* you sync with, not *how*.

### Depends On

R-01, R-02 (stable sync primitives).

### Unblocks

Scalability beyond ~20 peers.

---

## R-06: The Notebook Remembers, But You Can't Look Back ✓

> **Status: COMPLETE** — `store.as_of(physical_ms, logical)` returns a read-only `GraphSnapshot` with the graph state at any historical time. All query and algorithm methods available. O(n log n) per query.

Your notebook has every page ever written, in order. You could look up "what did we know yesterday at 3pm." The information is right there in the pages.

But Silk doesn't let you ask that question. It only shows you the latest version of the story. The past is in there, but locked away.

The fix: "read the notebook up to page 47 and stop." You replay the story but stop at a certain time. Now you can see what the world looked like at any point in the past.

This only works well with the real clock (R-01). With the finger-counting clock, "page 47" doesn't mean anything. With a real clock, "the state at 3:15pm yesterday" is a meaningful question.

### Research

- Hickey (2012) — Datomic: "The database is a value." Every query takes an `as-of` parameter.
- Salzberg & Tsotras (1999) — *Comparison of Access Methods for Time-Evolving Data*

### What Changes

New method: `MaterializedGraph::as_of(entries, cutoff_clock)`. Replays entries with clock <= cutoff. Returns a read-only graph snapshot.

Python API: `store.as_of(physical_ms, logical)` returns a `GraphSnapshot` object with all query methods but no mutation methods.

Performance: O(n) per historical query (full replay up to cutoff). For frequent time-travel, cache checkpoints at intervals — that's an optimization for later.

### Depends On

R-01 (HLC gives timestamps meaning).

### Unblocks

R-07 (Query Builder benefits from temporal queries — `Query(snapshot)` works on historical snapshots).

---

## R-07: Show Me, Don't Make Me Look ✓

> **Status: COMPLETE (foundation)** — Fluent `Query` builder + `QueryEngine` extension protocol. Covers 90% of use cases via Python-native API. Datalog/SPARQL can be plugged in via `QueryEngine` without changing Silk core. `from silk import Query, QueryEngine`.

Right now, asking a question about the graph is like asking a librarian "who are you connected to? Now who is that person connected to? Now filter by..." — step by step, in code.

What you want is to just ask the question: "Find me all servers running services that had critical alerts."

That's what a query language does. Specifically, Datalog is a query language that's like making statements and asking "what matches?"

```
I want X and Y where:
  X is a server,
  X runs Y,
  Y had a critical alert.
```

Silk figures out the answer. You don't have to tell it how to search.

Under the hood, the graph is just facts: "server-1 is a server," "server-1 runs api-svc," "alert-7 is critical." Datalog matches patterns across facts. It's like a crossword puzzle — fill in the variables so everything fits.

### Research

- Tonsky (2014) — DataScript: useful Datalog engine in ~1,000 LOC over an in-memory triple store
- Abiteboul, Hull & Vianu (1995) — *Foundations of Databases* (Datalog semantics)
- Whaley & Lam (2004) — Datalog for large-scale graph analysis

### What Was Built

Foundation layer in pure Python (`python/silk/query.py`):

```python
from silk import Query

# Find all down services running on active servers
results = (
    Query(store)
    .nodes("server")
    .where(status="active")
    .follow("RUNS")
    .where(status="down")
    .collect()
)
```

Fluent API: `.nodes()` → `.where()` → `.follow()` → `.where()` → `.collect()`. Chains filter and traversal operations. Works with both `GraphStore` (live) and `GraphSnapshot` (historical).

Extension protocol (`QueryEngine`): anyone can plug in Datalog, SPARQL, or a custom query language:

```python
from silk import Query, QueryEngine

class DatalogEngine:
    def execute(self, store, query):
        # Parse Datalog, evaluate against store, return results
        ...

results = Query(store, engine=DatalogEngine()).raw("?- node(X, 'server').")
```

The Datalog engine described in the original roadmap is an optional community contribution, not core Silk. The foundation (fluent builder + extension protocol) ships in core.

### Depends On

Benefits from R-06 (time-travel queries over Datalog), but no hard dependency.

### Unblocks

Declarative querying — transforms usability for knowledge graph workloads.

---

## R-08: The Notebook Gets Too Thick

You never erase pages. Never. That's the rule. It's what makes everything work — everyone can check everyone's pages, no one loses information.

But after a year, the notebook is 10,000 pages long. It takes forever to copy. It takes forever to read from the beginning. Most of those pages don't matter anymore — they say things like "the cat's color was changed from blue to red" from six months ago. Nobody cares about the blue-to-red change. The cat is red now. That's all that matters.

The fix: once ALL your friends have read past a certain page, everything before that page is "settled." Nobody will ever send you a page from before that point. So you can write a summary page — "here's what the world looks like right now" — and throw away everything before it.

The summary page becomes the new beginning of the notebook. It captures everything important. Nothing is lost. But the notebook is thin again.

### Research

- Baquero, Almeida & Shoker (2014) — *Making Operation-based CRDTs Operation-based* (causal stability)
- Shapiro & Baquero (2016) — *CRDTs with bounded tombstones* (safe GC conditions)
- Almeida, Shoker & Baquero (2018) — *Delta State Replicated Data Types* (delta interval GC)

### Safety Rule

A checkpoint at entry E is safe **if and only if** ALL known peers have synced past E. This means every peer's last-seen head is a descendant of E. No peer can send an entry concurrent with or before E that hasn't been seen.

### What Changes

New `GraphOp::Checkpoint` variant containing the full materialized state at a point in time. New `CompactionTracker` that records each peer's oldest retained entry (communicated via `SyncOffer`). When all peers report oldest >= candidate, compaction is safe.

After compaction: `[checkpoint] → [remaining entries] → [head]`. The checkpoint becomes the new genesis. New peers joining after compaction get the checkpoint as their starting point (equivalent to snapshot bootstrap, already supported).

### Why Last

This is the most dangerous. If you summarize too early — before all your friends have caught up — someone might send you an old page that conflicts with your summary, and you won't be able to reconcile.

You need the proof (R-04) to know what invariants compaction must preserve. You need the real clock (R-01) to know *when* it's safe. You need the rules checking (R-02) to make sure the summary doesn't include quarantined pages. You need ontology evolution (R-03) to capture the full schema in the checkpoint. You need everything else first.

### Depends On

R-01, R-02, R-03, R-04.

### Unblocks

Production viability at scale.
