# Silk-Graph: Deep Review (Post R-01 through R-08)

> Independent review of silk-graph at v0.1.3 through the lens of distributed systems
> research, graph database theory, and competitive landscape.
> 7,892 LOC, 13 Rust modules, ~140 Rust tests, 154 Python tests, 8 examples.

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

### 5. Incremental Materialization Diverges on Concurrent Schema Conflicts

**Severity: Correctness — Theorem 1 violation**

`merge_entries_vec()` (`python.rs:1117-1156`) only applies NEW entries incrementally, not replaying old ones. When two peers independently create conflicting `ExtendOntology` entries (same type name, different definitions), each peer processes its own extension first and quarantines the other's.

**Scenario:**
- Peer A: `ExtendOntology { "container": {image: required} }` (EA)
- Peer B: `ExtendOntology { "container": {runtime: required} }` (EB)
- Bidirectional sync: both now have {EA, EB}
- A quarantines EB (applied EA first locally). B quarantines EA (applied EB first locally).
- Identical entry sets, DIFFERENT materialized graphs.

`rebuild()` (`graph.rs:189-197`) clears quarantine and replays in deterministic topo order — this WOULD produce identical results. But `merge_entries_vec()` never calls `rebuild()`.

**Reference:** Balegas et al. (2015) — "Putting Consistency Back into Eventual Consistency": for invariant-preserving CRDTs, the result must be independent of delivery order.

**Fix:** Trigger `rebuild()` when `ExtendOntology` is quarantined during sync, OR implement deterministic LWW resolution for conflicting type definitions (higher HLC wins, applied both locally and during sync).

### 6. Checkpoint Discards Per-Property Clock Metadata

**Severity: Correctness — future LWW divergence**

Checkpoint synthetic ops all use the checkpoint's single clock (`graph.rs:100-106`):
```rust
let synthetic = Entry::new(
    op.clone(), vec![], vec![],
    entry.clock.clone(),  // checkpoint's clock for ALL ops
    &entry.author,
);
```

Original per-property LWW clocks (which tracked *who* last updated *each* property) are replaced by one timestamp. After compaction, a write at time T where `original_clock < T < checkpoint_clock` will win on a non-compacted peer (T > original_clock) but lose on the compacted peer (T < checkpoint_clock).

**Reference:** Almeida, Shoker & Baquero (2018), Section 5.3: "State-based GC must preserve the causal metadata required for correct future merges."

**Fix:** Extend Checkpoint format to carry original per-property clock metadata.

### 7. Compacted-to-Non-Compacted Sync Doubles the Oplog

**Severity: Protocol gap**

After compaction, peer A has one entry (checkpoint, `next=[]`). Syncing with non-compacted peer B:
1. B receives checkpoint (accepts it — empty `next` passes parent check)
2. B now has BOTH original entries AND checkpoint — data exists twice
3. B's oplog has two root entries with `next=[]` (genesis and checkpoint)
4. When B syncs with C, it sends everything including the redundant checkpoint
5. Oplog grows with every mixed-compaction sync

No protocol mechanism exists for a checkpoint to replace originals on the receiver.

### 8. HLC Tiebreaker Is Reversed and Undocumented

From `clock.rs`:
```rust
.then_with(|| other.id.cmp(&self.id)) // lower id wins
```

Lower instance ID wins ties. This is opposite to most CRDT implementations. Users naming instances "aaa-primary" vs "zzz-secondary" unintentionally create a priority hierarchy. Not documented in PROOF.md or PROTOCOL.md.

**Reference:** Shapiro et al. (2011) — tiebreaker must be total and deterministic. Both orderings are valid. The choice should be documented.

### 9. Gossip RNG Thundering Herd

Seed is `now_ms()` only. NTP-synchronized peers calling `select_sync_targets()` within the same millisecond select identical targets — creating a "thundering herd" on popular targets while ignoring others.

**Fix:** `seed = now_ms() ^ hash(instance_id)`.

---

## III. Cracks in the Graph Model

### 10. No Weighted Edges, No Numeric Graph Algorithms

All algorithms are unweighted. `shortest_path()` is BFS (fewest hops, not minimum cost). No Dijkstra, no PageRank, no betweenness centrality. For a knowledge graph with confidence scores on relationships, this is limiting.

Neo4j has GDS (Graph Data Science) library with 60+ algorithms. NetworkX has comprehensive weighted graph support. Users who need these will fragment their stack.

### 11. No Hyperedges, No Reification, No Named Graphs

Binary edges only. "Bob SAID that Alice KNOWS Carol" requires an intermediate node. RDF has native reification. For knowledge graphs, "who said what, when, with what confidence" is the core use case.

**Reference:** Angles, Arenas, Barcelo, Hogan, Reutter & Vrgoc (2017) — "Foundations of Modern Query Languages for Graph Databases."

### 12. Ontology Less Expressive Than OWL-Lite

Cannot express: cardinality constraints, range constraints, inverse properties, transitivity, disjointness.

**Counterargument:** OWL-DL is NEXPTIME-complete. Silk trades expressiveness for simplicity. For many KG use cases, type constraints + required properties + open properties (D-026) are sufficient.

**Reference:** Horrocks, Patel-Schneider & van Harmelen (2003) — "From SHIQ and RDF to OWL."

### 13. Edge Source/Target Type Validation Skipped on Sync

Only edge type existence is checked (`graph.rs:152`). Source/target type constraints are not validated. A synced edge can violate `source_types`/`target_types` constraints defined in the ontology.

---

## IV. Cracks in the Distributed Protocol

### 14. Unbounded Tombstone Regrowth After Compaction

R-08 compaction excludes tombstoned nodes. But post-compaction removes create new tombstones that grow without bound. Compaction is a one-time reset, not continuous.

**Reference:** Shapiro & Baquero (2016) — "Tombstones should be garbage-collected when causal stability is reached."

### 15. No Partial Sync

Every peer holds the entire graph. No mechanism for "sync only nodes of type X" or "sync only the last hour." Mobile/IoT clients that need a subtree must store everything.

Ditto has subscriptions. PowerSync has sync rules. Electric SQL has shapes.

### 16. Convergence Proof Doesn't Cover Compaction

PROOF.md Theorem 3 proves convergence for two peers exchanging entries. It doesn't prove compacted-to-non-compacted peers converge correctly.

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

### Fixable Cracks (Correctness)
1. Concurrent schema conflicts diverge materialized graphs (Theorem 1 violation)
2. Checkpoint discards per-property clocks (future LWW divergence)
3. Mixed-compaction sync doubles the oplog (no replacement protocol)
4. Edge source/target validation skipped on sync

### Structural Limitations (Design Decisions)
5. No partial sync (mobile/IoT blocked)
6. No weighted graph algorithms
7. No access control
8. No networking layer
9. Ontology less expressive than OWL-Lite
10. Tombstone regrowth after compaction

---

*Academic references: Balegas et al. 2015, Almeida et al. 2018, Shapiro et al. 2011, Shapiro & Baquero 2016, Kulkarni et al. 2014, Angles et al. 2017, Horrocks et al. 2003, Kleppmann & Beresford 2017, Demers et al. 1987.*
