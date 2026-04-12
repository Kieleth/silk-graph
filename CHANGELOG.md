# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-04-12

### Added
- **Ontology convergence** — `ontology_hash()` returns a deterministic BLAKE3 hash of the resolved ontology. `ontology_fingerprint()` returns a sorted list of atomic structural facts (types, properties, subtypes, constraints, parent relationships). `check_ontology_compatibility(hash, fingerprint)` returns "identical", "superset", "subset", or "divergent". Under additive-only evolution (R-03), a newer ontology's fingerprint is always a strict superset. No external dependencies. See [FAQ.md](FAQ.md#how-does-silk-detect-ontology-drift-between-peers).
- **Cursor-based tail subscriptions** (C-1) — `store.subscribe_from(cursor)` returns a `TailSubscription` that streams entries past the cursor via `next_batch(timeout_ms, max_count)`. The oplog is the buffer; no per-subscriber in-memory queue. Kafka-style semantics: resumable across reconnects, no drop policy, no backpressure on the producer. Works for both local appends and entries arriving via `merge_sync_payload`. Stale cursors (after compaction) raise `ValueError`; `register_subscriber_cursor(cursor)` blocks compaction while the subscriber is behind. Measured producer-side overhead with zero active subscribers: <2% at 1k appends, 0% at 10k. Wake-up latency: p50 0.16ms, p99 0.22ms. See [FAQ.md](FAQ.md#how-do-i-tail-silks-oplog-like-kafka) and `examples/tail_subscription.py`.
- **`DefineLens` GraphOp variant** — reserved for future schema transformation lenses. No-op today.
- **`Entry.ontology_hash`** — optional field, stamped on future entries. Not part of content hash.

## [0.1.7] - 2026-03-27

### Added
- **RDFS-level class hierarchy** — `parent_type` on NodeTypeDef declares is-a relationships. Property inheritance from ancestors, hierarchy-aware queries (`query_nodes_by_type` returns descendants), hierarchy-aware edge validation (`source_types: ["entity"]` accepts server). Fully CRDT-compatible. See [FAQ.md](FAQ.md#does-silk-support-class-hierarchies-or-type-inheritance).
- **Extended constraint vocabulary** — SHACL-inspired property constraints: `pattern` (full regex via `regex` crate), `min_length`/`max_length` (string length), `min_exclusive`/`max_exclusive` (exclusive numeric bounds). All enforced on both `add_node` and `update_property`. See [FAQ.md](FAQ.md) for the full constraint reference table.
- **Compaction safety enforcement** — `compact()` now checks all registered peers have synced before compacting. Raises `RuntimeError` if any peer hasn't synced. `verify_compaction_safe()` for explicit checks. Pass `safe=False` to override.
- **`memory_usage()`** — returns Rust-side heap estimates (`oplog_bytes`, `graph_bytes`, `total_bytes`).
- **Sync compression** — optional, pluggable `SyncCompression` protocol. Built-in: `ZlibCompression(level=1)` (68% bandwidth savings, 29% latency overhead), `NoCompression`. Custom compressors implement `compress()` + `decompress()`. See [FAQ.md](FAQ.md).
- **OperationBuffer** — filesystem-backed write-ahead buffer for graph operations. Buffer ops as JSONL when the store isn't available (boot time, pre-init), drain into a live store when it opens. Rust core (`src/buffer.rs`) + Python binding. Explicit drain, no sync participation, ontology validated at drain time. See [FAQ.md](FAQ.md#how-do-i-buffer-operations-before-the-store-is-open).
- **Fault injection experiments** — 8 scenarios: message loss, corruption, truncation, duplicate delivery, 50% random loss, three-peer partition, concurrent conflicts, rapid fire. All pass. See [EXPERIMENTS.md](EXPERIMENTS.md).
- **Deferred flush mode** — `store.set_flush_mode("deferred")` buffers writes in memory, persists on `store.flush()`. 276x faster than immediate mode for bulk writes (one fsync vs N). See [FAQ.md](FAQ.md) and [EXP-08](EXPERIMENTS.md).

### Fixed
- **Sync ancestor closure O(n×depth) → O(n)** — BFS queue replaces nested loop. 16x faster at 99% overlap. See [EXP-01](EXPERIMENTS.md).
- **Compaction per-property clock loss** — checkpoint now emits per-property `UpdateProperty` ops with individual clocks. See [EXP-02](EXPERIMENTS.md).
- **Store write amplification** — `append()` batches entry + heads into single redb transaction (was 2). `merge()` batches all entries + heads into one transaction.
- **`reconstruct_oplog()` O(n²) → O(n)** — topological BFS replaces retry loop. Handles multi-root stores after cross-peer sync.
- **Quarantine un-quarantine notification** — subscribers now notified when previously-quarantined entries become valid after ontology evolution.
- **Ghost feature: DFS** — documented but not implemented. Now implemented (`store.dfs()`).
- **Ghost feature: `refs` skip-list** — documented as "16 refs per entry" but always empty. Corrected to "reserved, currently unused."
- **DESIGN.md stale pseudocode** — Entry struct fields updated to match actual code (HybridClock, String author, Optional signature).
- **PROOF.md quarantine lifecycle** — clarified that quarantine is grow-only per pass but cleared on rebuild.

### Changed
- **Refactor: python.rs split into modules** — `src/python.rs` (1,983 lines) split into `python/mod.rs` (1,473), `python/conversions.rs` (297), `python/snapshot.rs` (166), `python/obslog.rs` (91). No API changes.
- **Refactor: ontology constraint validation** — `validate_constraints()` (162 lines) refactored to 55 lines via extracted helpers (`check_numeric_bound`, `check_string_length`, `constraint_err`).
- **Refactor: graph LWW deduplication** — `merge_properties_lww()` extracted as single source of truth for per-property LWW, used by both `apply_add_node()` and `apply_add_edge()`.
- **Comparative benchmarks** — Silk vs Loro vs pycrdt (8 scenarios), Silk vs NetworkX vs TerminusDB (graph system comparison). Docker reproducible. See [BENCHMARKS.md](BENCHMARKS.md).
- **Experiments** — 6 experiments (EXP-01 through EXP-07) with structured metrics and regression tests. See [EXPERIMENTS.md](EXPERIMENTS.md).

## [0.1.6] - 2026-03-25

### Fixed
- **UpdateProperty validation** — `update_property()` now validates property types and constraints (`enum`, `min`, `max`) against the ontology before applying. Previously, type mismatches and constraint violations were silently accepted. Unknown properties still accepted (D-026: ontology defines minimum, not maximum).

## [0.1.5] - 2026-03-24

### Added
- **Property constraints** — `enum`, `min`, `max` on PropertyDef. Extensible via `validate_constraints()` in ontology.rs. Unknown constraints ignored (forward compat).
- **Compaction policies** — `IntervalPolicy`, `ThresholdPolicy`, `CompactionPolicy` protocol. Automate when to compact.
- **GraphView** — filtered projection over stores/snapshots. `GraphView(store, node_types=["server"])`. Edges filtered by both endpoints.
- **Filtered sync** — `receive_filtered_sync_offer(offer, node_types)`. Best-effort bandwidth reduction with causal closure.
- **FAQ.md** — 10 questions from expert reviews: algorithms, schema, compaction, partial sync, extensibility.

## [0.1.4] - 2026-03-23

### Fixed
- **Bug 5: Concurrent schema conflicts** — `ExtendOntology` entries during sync now trigger a full graph `rebuild()` instead of incremental apply. Deterministic topological order ensures identical quarantine sets across all peers. Fixes Theorem 1 violation.
- **Bug 6: Checkpoint per-property clocks** — Checkpoint now preserves per-entity max clocks (`op_clocks` field). Synthetic entries use original clocks, not the checkpoint clock. Prevents LWW divergence after compaction.
- **Bug 7: Mixed-compaction sync doubled oplog** — When a Checkpoint entry (next=[]) is merged into a non-compacted oplog, it now replaces the entire oplog instead of creating a second root.
- **Bug 9: Gossip thundering herd** — Peer selection seed now includes instance ID hash (`now_ms ^ fnv(instance_id)`). NTP-synchronized peers select different targets.
- **Bug 13: Edge source/target validation on sync** — `AddEdge` in the quarantine path now validates source/target node type constraints when both endpoints are materialized.

### Added
- **R-07: Query Builder** — Fluent `Query` class for Python-native graph queries. Chain `.nodes()`, `.where()`, `.follow()`, `.collect()`. Works with both `GraphStore` and `GraphSnapshot`. `QueryEngine` protocol for plugging in Datalog/SPARQL/custom engines.
- **R-08: Epoch Compaction** — `store.compact()` compresses the entire oplog into a single checkpoint entry. Preserves all live nodes, edges, and ontology extensions. Tombstoned entities excluded. Works with persistent stores. `create_checkpoint()` for inspection without compacting.

## [0.1.3] - 2026-03-23

### Added
- **R-06: Time-Travel Queries** — `store.as_of(physical_ms, logical)` returns a read-only `GraphSnapshot` with the graph state at any historical time. All query and graph algorithm methods available. New `GraphSnapshot` class exported from Python.
- **Dict ontology API** — `GraphStore("id", {"node_types": {...}})` accepts Python dicts directly. No `json.dumps()` needed. `extend_ontology()` also accepts dicts.
- **Protocol versioning** — `SyncOffer.protocol_version` field enables future wire format changes without silent breakage.

## [0.1.2] - 2026-03-23

### Added
- **D-027: Author authentication** — ed25519 signatures on entries. `generate_signing_key()`, `set_signing_key()`, `get_public_key()`, `register_trusted_author()`, `set_require_signatures()`. Auto-sign on write, verify on merge. Strict mode rejects unsigned entries. Backward compatible — unsigned entries accepted by default.
- **R-02: Sync Quarantine** — Invalid entries from sync are now accepted into the oplog (preserving CRDT convergence) but quarantined from the materialized graph. `get_quarantined()` returns hex hashes of quarantined entries. Grow-only set — monotonic, safe. Local writes still reject immediately.
- **R-03: Monotonic Ontology Evolution** — `extend_ontology(json)` adds new node types, edge types, properties, and subtypes at runtime. Only additive changes allowed (monotonic). Concurrent extensions merge by union; conflicting same-name types quarantined (R-02). Extensions sync between peers and persist through snapshots.
- **R-05: Gossip Peer Selection** — `register_peer()`, `select_sync_targets()`, `record_sync()`. Logarithmic fan-out: `ceil(ln(N) + 1)` peers per round. Scales from 2 peers (all-to-all) to 10,000+ (gossip). No changes to sync protocol.
- **R-04: Formal Convergence Proof** — `PROOF.md` documents three convergence theorems (deterministic materialization, idempotent merge, convergence after bidirectional sync), six invariants, and addenda for quarantine and ontology evolution. Semi-formal, code-referenced.

### Security
- **S-01**: HybridClock logical counter uses `saturating_add` — prevents overflow wrap-around at u64::MAX
- **S-03**: Sync message size limits — 64 MB max bytes, 100K max entries per payload
- **S-04 → R-02**: Ontology validation on sync — superseded by R-02 quarantine model. Invalid entries accepted into oplog (CRDT convergence), quarantined from materialized graph.
- **S-05**: Bloom filter dimension validation — rejects malformed bloom filters that would cause panics
- **S-06**: pyo3 version pinned to >=0.23.4 (RUSTSEC-2025-0020)
- **S-09**: redb databases created with 0600 permissions on Unix (owner-only)
- **S-10**: Value nesting depth limit (64 levels) — prevents stack overflow from deeply nested structures
- **S-12**: Value size limits — strings capped at 1 MB, lists/maps at 10K items
- **S-13**: ObservationLog rejects source names > 65535 bytes instead of silently truncating
- **S-20**: Default features changed to `[]` — pyo3 is opt-in, not pulled by default for Rust consumers
- **S-01b**: Clock drift rejection on sync — entries with physical_ms exceeding local physical_ms + 1,000,000 ms are rejected (MAX_CLOCK_DRIFT = 1,000,000)

### Changed
- **R-01: Hybrid Logical Clocks** — BREAKING: Replace LamportClock with HybridClock. Entries now carry wall-clock time (physical_ms) and logical counter. LWW conflicts resolved by real-time ordering. All entry hashes change — v0.1 stores incompatible.
- **D-026: Open properties** — Unknown properties are now accepted without validation. Unknown subtypes are accepted with type-level validation only. The ontology defines the minimum, not the maximum. Previously, any property or subtype not declared in the ontology was rejected with `ValidationError`.

## [0.1.0] - 2026-03-21

### Added
- Ontology-enforced schema validation (node types, edge types, properties)
- Content-addressed Merkle-DAG (BLAKE3 hashing)
- Lamport clock for causal ordering
- Materialized graph view with live node/edge queries
- Per-property last-writer-wins conflict resolution (D-021)
- Delta-state CRDT sync with Bloom filter optimization
- Sync protocol: generate_offer / receive_offer / merge_payload
- Snapshot-based full state transfer
- Graph algorithms: BFS, shortest path, impact analysis, pattern matching, topological sort, cycle detection
- Persistent storage via redb (embedded, transactional)
- Real-time subscriptions with error isolation (D-023)
- Subtype support for domain specialization (D-024)
- ObservationLog: append-only TTL-pruned time-series store (D-025)
- Python bindings via PyO3/maturin
- 108 Rust tests + 100+ Python integration tests
