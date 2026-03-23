# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **D-027: Author authentication** — ed25519 signatures on entries. `generate_signing_key()`, `set_signing_key()`, `get_public_key()`, `register_trusted_author()`, `set_require_signatures()`. Auto-sign on write, verify on merge. Strict mode rejects unsigned entries. Backward compatible — unsigned entries accepted by default.
- **R-02: Sync Quarantine** — Invalid entries from sync are now accepted into the oplog (preserving CRDT convergence) but quarantined from the materialized graph. `get_quarantined()` returns hex hashes of quarantined entries. Grow-only set — monotonic, safe. Local writes still reject immediately.
- **R-03: Monotonic Ontology Evolution** — `extend_ontology(json)` adds new node types, edge types, properties, and subtypes at runtime. Only additive changes allowed (monotonic). Concurrent extensions merge by union; conflicting same-name types quarantined (R-02). Extensions sync between peers and persist through snapshots.
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
