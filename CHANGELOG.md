# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
