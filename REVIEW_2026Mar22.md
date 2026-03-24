# Silk-Graph: Independent Review

> Review conducted 2026-03-22 against v0.1.3 (commit `9d9365d`).
> Responses and fixes applied through v0.1.5 (commit `4bbf108`).
>
> **Current status:** All correctness bugs fixed. Structural limitations acknowledged with responses. See [FAQ.md](FAQ.md) for user-facing answers.

---

## Genuine Value

**1. The Ontology-CRDT Coupling Is Novel.** No existing system combines schema-enforced property graphs with delta-state CRDTs on a Merkle-DAG.

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

Silk's quarantine (R-02) + monotonic evolution (R-03) creates a unique design point: the schema is itself a CRDT (add-only set of type definitions) that co-evolves with the data. This is closer to Description Logic's Open World Assumption than any competitor implements.

**The value:** Schema enforcement at write time + convergence guarantee after sync = both correctness AND availability. Strong eventual consistency (Shapiro et al. 2011) with schema constraints that typical AP systems lack.

**2. The Append-Only Merkle-DAG Is the Right Abstraction.** Content-addressed entries with causal links give deduplication, integrity, causal ordering, and audit trails. The combination with HLC (R-01) gives DAG entries real-time meaning.

**3. The Proof Is Unusually Rigorous for v0.** [PROOF.md](PROOF.md) states three theorems with six invariants plus addenda for quarantine, ontology evolution, and compaction. Most CRDT libraries have "we tested it."

**4. Clean Rust/Python Stack.** Zero `unsafe` blocks. PyO3 bindings with full type stubs. Transport-agnostic sync (pure functions over bytes).

---

## Resolved Issues (v0.1.4)

All correctness bugs identified in the original review have been fixed.

| # | Issue | Fix | Severity |
|---|-------|-----|----------|
| 5 | Concurrent schema conflicts diverged materialized graphs | Full `rebuild()` on schema changes — deterministic topo order | Critical → Fixed |
| 6 | Checkpoint discarded per-property clock metadata | `op_clocks` field preserves per-entity max clocks | Critical → Fixed |
| 7 | Mixed-compaction sync created second oplog root | Checkpoint entries replace oplog on merge | Protocol → Fixed |
| 8 | HLC tiebreaker undocumented | Documented in [PROTOCOL.md](PROTOCOL.md): lower ID wins | Documentation → Fixed |
| 9 | Gossip RNG thundering herd | Seed includes `fnv(instance_id)` | Gossip → Fixed |
| 13 | Edge source/target validation skipped on sync | `validate_edge()` when both endpoints materialized | Validation → Fixed |
| 16 | Convergence proof didn't cover compaction | [PROOF.md](PROOF.md) Section 6: compaction addendum | Proof → Fixed |

---

## Open Items — Scope Boundaries

These are design decisions, not bugs. Each has a documented rationale and, where applicable, a partial solution or extension point.

### Algorithms are navigation primitives, not analytics (#10)

Silk is a distributed sync layer. Built-in algorithms (BFS, shortest_path, impact_analysis, pattern_match) answer "what's connected?" — not "what's the optimal route?" For analytics (Dijkstra, PageRank, centrality), use NetworkX/igraph on top of Silk's graph data. The [`QueryEngine`](QUERY_EXTENSIONS.md) protocol is the integration point.

### Binary edges only — no hyperedges or reification (#11)

Silk enforces structural contracts (types, connections, properties), not semantic expressiveness. Model n-ary relationships with intermediate nodes — the property graph standard (Neo4j, TigerGraph, Neptune). Edge properties ([D-026](DESIGN.md)) carry arbitrary metadata. See [FAQ.md](FAQ.md).

### Ontology simpler than OWL-Lite (#12)

Partially addressed. Built-in constraints now include `enum` (allowed values) and `min`/`max` (numeric range). Unknown constraint names are silently ignored (forward compatibility for community validators). Semantic reasoning (transitivity, inverse properties) is out of scope — use a reasoner on top. See [FAQ.md](FAQ.md).

### Tombstone regrowth after compaction (#14)

Addressed via compaction policies. `store.compact()` is repeatable — each call produces a clean checkpoint. Built-in: `IntervalPolicy(seconds)`, `ThresholdPolicy(max_entries)`. Custom policies implement the `CompactionPolicy` protocol. See [FAQ.md](FAQ.md).

### Partial sync (#15)

Partially addressed. `GraphView` provides query-time filtering (full oplog, filtered materialization). `receive_filtered_sync_offer()` provides best-effort bandwidth reduction with causal closure. True partial replication (fragmented DAGs) is tracked in a research branch. See [FAQ.md](FAQ.md).

---

## Open Items — Known Gaps

### Query builder limitations (#VI)

The fluent `Query` API covers simple traversals and filters. It does not support joins, aggregation, variable-length paths, subqueries, or negation. The `QueryEngine` extension protocol ([QUERY_EXTENSIONS.md](QUERY_EXTENSIONS.md)) is the escape hatch — Datalog, SPARQL, or Cypher can be plugged in without changing Silk core. Property indexes (for `.where()` performance at 100K+ nodes) are a future optimization.

### Access control (#VII-1, highest priority)

D-027 authenticates WHO (ed25519 signatures). It does NOT authorize WHAT. No per-peer write permissions, no per-type access rules. This is the most impactful missing feature for production multi-tenant deployments. Requires a trust policy layer above the signature system.

### Networking layer (#VII-2)

Transport-agnostic sync is clean, but every user must build peer discovery, connection management, framing, and authentication. Automerge has `automerge-repo`. Ditto has built-in mesh. A `silk-net` package (peer discovery + TCP/QUIC framing) would lower the barrier to entry significantly.

### Streaming / reactive queries (#VII-3, lowest priority)

Subscriptions fire on every entry. No filter predicates. No "notify me when type X with property Y > 10." For most use cases, polling with `Query` is sufficient. Filtered subscriptions are a quality-of-life improvement, not a blocker.

---

## Competitive Landscape

| System | Strengths | Weaknesses vs Silk |
|--------|-----------|-------------------|
| **Automerge** | Formally verified (Isabelle/HOL). Rich text CRDT. Multi-language. Larger community. `automerge-repo` for networking. | No graph primitives, no schema, no ontology. JSON documents, not knowledge graphs. |
| **TerminusDB** | WOQL queries. Delta-rollback time-travel. RDF/OWL integration. Schema migration. | Centralized. No CRDT. No offline-first. No peer-to-peer sync. |
| **Ditto** | Production-proven (Japan Airlines, US Air Force). Partial sync. Mesh networking. BFT. | Closed source. Document-oriented. No ontology. Expensive licensing. |
| **Electric SQL / PowerSync** | Postgres compatibility. Partial sync. SQL ecosystem. | Centralized authority (Postgres is source of truth). Not true CRDT. No graph primitives. |
| **RDF / SPARQL** | W3C standard. OWL reasoning. Massive scale (Wikidata: 15B triples). Formal semantics. | Centralized. No offline-first. No CRDT sync. Complex stack. Steep learning curve. |
| **cr-sqlite** | SQLite compatibility. Row-level CRDTs. Familiar SQL. | Relational, not graph. No schema enforcement beyond SQL types. Limited conflict resolution. |

**Silk's niche:** Schema-enforced property graphs with offline-first CRDT sync. No competitor occupies this exact position. The closest (Automerge for sync, TerminusDB for schema) each lack the other half.

---

## Summary

### Defensible Value
1. Ontology-CRDT coupling (unique in the field)
2. Merkle-DAG with HLC (content-addressed + real-time ordering)
3. Semi-formal convergence proof (unusually rigorous for v0)
4. Clean Rust/Python stack (zero unsafe, type stubs)
5. Transport-agnostic sync (pure functions over bytes)
6. Monotonic ontology evolution (add-only schema CRDT)

### All Correctness Bugs Fixed (v0.1.4)
Schema conflicts, checkpoint clocks, mixed-compaction sync, edge validation, gossip RNG, HLC documentation, convergence proof coverage.

### Scope Boundaries (by design)
- Analytics → use NetworkX on top ([`QueryEngine`](QUERY_EXTENSIONS.md) integration point)
- Hyperedges → intermediate nodes (property graph standard)
- OWL expressiveness → enum/range constraints + extensible validators
- Tombstone growth → compaction policies ([`CompactionPolicy`](python/silk/compaction.py) protocol)
- Partial sync → `GraphView` + filtered sync (true partial replication in research branch)

### Open Gaps (prioritized)
1. **Access control** — highest impact, needs trust policy layer
2. **Networking layer** — highest adoption friction, needs `silk-net` package
3. **Query composability** — `QueryEngine` is the escape hatch, property indexes needed for scale
4. **Reactive subscriptions** — quality-of-life, not blocking

---

*Academic references: Balegas et al. 2015, Almeida et al. 2018, Shapiro et al. 2011, Shapiro & Baquero 2016, Kulkarni et al. 2014, Angles et al. 2017, Horrocks et al. 2003, Kleppmann & Beresford 2017, Demers et al. 1987.*
