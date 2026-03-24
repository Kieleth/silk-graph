# Silk-Graph: Deep Review (Post R-01 through R-08)

> Independent review of silk-graph, through the lens of distributed systems
> research, graph database theory, and competitive landscape.
>
> **Review status (v0.1.4):** All correctness bugs fixed. Structural limitations acknowledged as design decisions.

---

## I. Where Silk Has Genuine Value

### 1. The Ontology-CRDT Coupling Is Novel

No existing system combines schema-enforced property graphs with delta-state CRDTs on a Merkle-DAG.

| Contender | Schema | Graph-Native | CRDT | Offline-First |
|-----------|--------|-------------|------|---------------|
| **Silk** | Ontology (typed, enforced) | BFS, shortest path, pattern match | Delta-state on Merkle-DAG | Yes |
| Automerge | None | None | OpSet (JSON) | Yes |
| OrbitDB | None | None | Merkle-CRDT on IPFS | Yes |
| GUN | None | Graph-like API | Custom (convergence issues) | Yes |
| cr-sqlite | SQL schema | Relational | Row-level CRDTs | Yes |
| Electric SQL | Postgres schema | Relational | Centralized authority | Partial |
| Ditto | None | Document-oriented | Delta-state | Yes |
| TerminusDB | RDF/OWL | WOQL queries | None (centralized) | No |
| Neo4j | Labels + indexes | Cypher, GDS library | None (centralized) | No |

Silk's R-02 (quarantine) + R-03 (monotonic evolution) creates a unique design point: the schema is itself a CRDT (add-only set of type definitions) that co-evolves with the data. This is closer to Description Logic's Open World Assumption than any competitor implements.

**The value:** Schema enforcement at write time + convergence guarantee after sync = both correctness AND availability. CAP theorem's AP partition with strong eventual consistency — but with schema constraints that typical AP systems lack.

### 2. The Append-Only Merkle-DAG Is the Right Abstraction

Content-addressed entries with causal links give you deduplication, integrity, causal ordering, and audit trails — for free. This is the same insight behind Git, IPFS, and Certificate Transparency. The combination with HLC (R-01) gives DAG entries real-time meaning — unlike Git commits with wall-clock timestamps but Lamport-ordered parents.

### 3. The Proof Is Unusually Rigorous for v0

PROOF.md states three theorems with six invariants. Most CRDT libraries have "we tested it." Silk has "here's why it works, and here's where to look if it doesn't." The assumptions section (no hash collisions, no Byzantine peers, reliable delivery) is honest.

### 4. Clean Rust/Python Stack

Zero `unsafe` blocks. PyO3 bindings with full type stubs. Transport-agnostic sync (pure functions over bytes). Monotonic ontology evolution.

---

## II. Cracks in the CRDT Theory

### 5. ~~Incremental Materialization Diverges on Concurrent Schema Conflicts~~

**FIXED in v0.1.4.** `merge_entries_vec()` now detects `ExtendOntology` or `Checkpoint` entries in the sync batch and triggers a full `rebuild()` instead of incremental apply. Deterministic topological order ensures identical quarantine sets across all peers, regardless of delivery order.

**Original severity:** Correctness — Theorem 1 violation.
**Reference:** Balegas et al. (2015) — "Putting Consistency Back into Eventual Consistency."

### 6. ~~Checkpoint Discards Per-Property Clock Metadata~~

**FIXED in v0.1.4.** `GraphOp::Checkpoint` now carries an `op_clocks: Vec<(u64, u32)>` field — one (physical_ms, logical) pair per synthetic op. `build_checkpoint_ops()` extracts the max per-property clock for each entity. During replay, `graph.apply()` uses these per-op clocks instead of the checkpoint's single clock. Future LWW comparisons use the correct original granularity.

**Original severity:** Correctness — future LWW divergence.
**Reference:** Almeida, Shoker & Baquero (2018), Section 5.3.

### 7. ~~Compacted-to-Non-Compacted Sync Doubles the Oplog~~

**FIXED in v0.1.4.** `oplog.append()` now detects incoming `Checkpoint` entries with `next=[]` and calls `replace_with_checkpoint()` instead of creating a second root. When a non-compacted peer receives a checkpoint, it replaces its entire oplog — no data duplication, no second root.

**Original severity:** Protocol gap.

### 8. ~~HLC Tiebreaker Is Reversed and Undocumented~~

**FIXED in v0.1.4.** PROTOCOL.md now explicitly documents: "Both equal → **lower** id wins (lexicographic, deterministic). Note: this means instance names create an implicit priority hierarchy. Both orderings (lower-wins, higher-wins) are valid per Shapiro et al. (2011); Silk chose lower-wins."

**Original severity:** Documentation gap. The ordering was always deterministic and correct; it just wasn't documented.

### 9. ~~Gossip RNG Thundering Herd~~

**FIXED in v0.1.4.** `PeerRegistry` now takes an instance ID at construction (`with_instance_id()`). The RNG seed is `now_ms() ^ fnv(instance_id)`. NTP-synchronized peers select different targets.

---

## III. Cracks in the Graph Model

### 10. No Weighted Edges, No Numeric Graph Algorithms

All algorithms are unweighted. `shortest_path()` is BFS (fewest hops, not minimum cost). No Dijkstra, no PageRank, no betweenness centrality. For a knowledge graph with confidence scores on relationships, this is limiting.

Neo4j has GDS (Graph Data Science) library with 60+ algorithms. NetworkX has comprehensive weighted graph support. Users who need these will fragment their stack.

> **Response (v0.1.4):** Out of scope — by design. Silk is the distributed sync layer, not a graph analytics engine. Section I of this review positions Silk against sync engines (Automerge, OrbitDB, Ditto); evaluating it against analytics engines (Neo4j GDS, NetworkX) is a category error.
>
> The intended architecture: two NetworkX instances on different servers, connected by Silk. Silk keeps the graph consistent. The application does analytics on top.
>
> The built-in algorithms (BFS, shortest_path, impact_analysis, pattern_match) are navigation primitives for graph traversal, not a competing analytics suite. `shortest_path()` is unweighted BFS following NetworkX naming convention (NetworkX defaults to unweighted too).
>
> The `QueryEngine` extension protocol (R-07) is the explicit integration point: a user who wants weighted shortest path writes a QueryEngine backed by NetworkX, registers it with `Query(store, engine=nx_engine)`, and gets `.raw("dijkstra(A, B, weight='latency')")`. The architecture already accounts for this — it's a designed boundary, not a gap.

### 11. No Hyperedges, No Reification, No Named Graphs

Binary edges only. "Bob SAID that Alice KNOWS Carol" requires an intermediate node. RDF has native reification. For knowledge graphs, "who said what, when, with what confidence" is the core use case.

**Reference:** Angles, Arenas, Barcelo, Hogan, Reutter & Vrgoc (2017) — "Foundations of Modern Query Languages for Graph Databases."

> **Response (v0.1.4):** Acknowledged as out of scope. Silk enforces structural contracts (types, connections, required properties), not semantic expressiveness (reification, hyperedges, transitivity). Applications model their domain using Silk's primitives — binary edges, intermediate nodes, rich edge properties (D-026) — whatever pattern fits. Silk syncs the result. The transport layer doesn't prescribe the modeling pattern above it.
>
> The intermediate node pattern is the industry standard for property graphs — Neo4j, TigerGraph, and Amazon Neptune all use it. RDF's original reification model was widely considered a failure (RDF-star was invented to replace it).
>
> Distinction: Silk's ontology is structural guardrails ("EMPLOYS connects organization→person"), not semantic modeling ("EMPLOYS is transitive"). It prevents malformed graphs. It doesn't encode domain semantics — that's the application's job.

### 12. Ontology Less Expressive Than OWL-Lite

Cannot express: cardinality constraints, range constraints, inverse properties, transitivity, disjointness.

**Counterargument:** OWL-DL is NEXPTIME-complete. Silk trades expressiveness for simplicity. For many KG use cases, type constraints + required properties + open properties (D-026) are sufficient.

**Reference:** Horrocks, Patel-Schneider & van Harmelen (2003) — "From SHIQ and RDF to OWL."

> **Response (v0.1.4):** Partially addressed. Silk enforces structural contracts, not semantic reasoning. The boundary:
>
> **Now supported** (structural validation):
> - Enum constraints: `"constraints": {"enum": ["active", "standby", "decommissioned"]}`
> - Range constraints: `"constraints": {"min": 1, "max": 65535}`
> - Unknown constraint names are silently ignored (forward compatibility for community-contributed validators)
>
> **Out of scope** (semantic reasoning):
> - Inverse properties, transitivity, disjointness — these infer new facts. That's a reasoner's job (Pellet, HermiT), not a sync engine's.
> - Cardinality constraints — requires graph context during validation (counting edges), which is a different API contract. File an RFC if you need this.
>
> Community contributions welcome: add new constraint types to `validate_constraints()` in `ontology.rs`. See [FAQ.md](FAQ.md) for the extension guide.

### 13. ~~Edge Source/Target Type Validation Skipped on Sync~~

**FIXED in v0.1.4.** `graph.apply()` for `AddEdge` now calls `ontology.validate_edge()` when both source and target nodes are materialized. If either endpoint is missing (out-of-order sync), validation is deferred to `rebuild()`. Invalid edges are quarantined.

---

## IV. Cracks in the Distributed Protocol

### 14. Unbounded Tombstone Regrowth After Compaction

R-08 compaction excludes tombstoned nodes. But post-compaction removes create new tombstones that grow without bound. Compaction is a one-time reset, not continuous.

**Reference:** Shapiro & Baquero (2016) — "Tombstones should be garbage-collected when causal stability is reached."

### 15. No Partial Sync

Every peer holds the entire graph. No mechanism for "sync only nodes of type X" or "sync only the last hour." Mobile/IoT clients that need a subtree must store everything.

Ditto has subscriptions. PowerSync has sync rules. Electric SQL has shapes.

### 16. ~~Convergence Proof Doesn't Cover Compaction~~

**FIXED in v0.1.4.** PROOF.md Section 6 (Compaction Addendum) now proves that compacted OpLogs converge with uncompacted peers. The proof covers: checkpoint safety rule, checkpoint construction correctness, snapshot fallback for mixed-compaction sync, and multi-peer compaction at different times.

---

## V. Competitive Landscape

### Automerge (Kleppmann et al.)

Formally verified (Isabelle/HOL). Rich text CRDT. Multi-language. Larger community. But: no graph primitives, no schema, no ontology. Different data model (JSON documents vs knowledge graphs).

### TerminusDB

WOQL queries. Delta-rollback time-travel. RDF/OWL integration. Schema migration. But: centralized, no CRDT, no offline-first.

### Ditto

Production-proven (Japan Airlines, US Air Force). Partial sync. Mesh networking. But: closed source, document-oriented, no ontology, expensive.

### Electric SQL / PowerSync

Postgres compatibility. Partial sync. But: centralized authority, not true CRDT, no graph primitives.

### RDF / SPARQL Ecosystem

W3C standard. SPARQL. OWL reasoning. Massive scale (Wikidata: 15B triples). But: centralized, no offline-first, no CRDT sync, complex stack.

---

## VI. Query Builder Gap

### The Fluent API Is Good But Not Composable

`Query(store).nodes("server").where(status="active").follow("RUNS").collect()` reads well. But: no joins, no aggregation, no variable-length path expressions, no subqueries, no negation.

Cypher: `MATCH (s:Server)-[:RUNS*1..3]->(svc) WHERE NOT (svc)-[:ALERTS]->() RETURN s, count(svc)`

The `QueryEngine` extension protocol is the escape hatch. But shipping with only the fluent builder means users hit a wall for non-trivial queries.

### No Property Indexes

`.where(status="active")` does a linear scan. No property index. For 100K nodes of type "server," this scans all 100K. Neo4j has composite indexes. Datomic has AVET indexes.

---

## VII. What's Genuinely Missing

1. **Access control** — D-027 authenticates WHO, but doesn't authorize WHAT. No per-peer write permissions.
2. **Networking layer** — transport-agnostic is clean, but every user must build peer discovery, connection management, framing, authentication. Automerge has `automerge-repo`. Ditto has built-in mesh.
3. **Streaming / reactive queries** — subscriptions fire on every entry. No filter predicates. No "notify me when type X with property Y > 10."

---

## VIII. Summary

### Defensible Value
1. Ontology-CRDT coupling (unique in the field)
2. Merkle-DAG with HLC (content-addressed + real-time ordering)
3. Semi-formal convergence proof (unusually rigorous for v0)
4. Clean Rust/Python stack (zero unsafe, type stubs)
5. Transport-agnostic sync (pure functions over bytes)
6. Monotonic ontology evolution (add-only schema CRDT)

### ~~Fixable Cracks (Correctness)~~ — All Fixed in v0.1.4
1. ~~Concurrent schema conflicts~~ → full rebuild on schema changes
2. ~~Checkpoint per-property clocks~~ → op_clocks field preserves metadata
3. ~~Mixed-compaction sync~~ → checkpoint replaces oplog on merge
4. ~~Edge source/target validation~~ → validate_edge when endpoints exist

### Structural Limitations (Design Decisions)
5. No partial sync (mobile/IoT blocked)
6. No weighted graph algorithms
7. No access control
8. No networking layer
9. Ontology less expressive than OWL-Lite
10. Tombstone regrowth after compaction

---

*Academic references: Balegas et al. 2015, Almeida et al. 2018, Shapiro et al. 2011, Shapiro & Baquero 2016, Kulkarni et al. 2014, Angles et al. 2017, Horrocks et al. 2003, Kleppmann & Beresford 2017, Demers et al. 1987.*
