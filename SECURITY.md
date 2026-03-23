# Security Policy

## Threat Model

Silk is designed for **trusted peer networks** — devices and services you control, syncing with each other. All peers share the same ontology and are assumed to be non-malicious.

### What's protected (v0.1)

- **Hash integrity** — `verify_hash()` rejects tampered entries (BLAKE3 over canonical MessagePack)
- **Duplicate entries** — idempotent skip, replay-safe
- **Missing parents** — rejected with MissingParent error
- **Clock overflow** — saturating arithmetic prevents u64 wrap-around (S-01)
- **Clock drift rejection** — entries with physical_ms exceeding local physical_ms + 1,000,000 ms are rejected on sync, preventing the "Byzantine clock" attack where a malicious peer wins all LWW conflicts (S-01b)
- **Bloom filter crashes** — malformed dimensions validated after deserialization (S-05)
- **Message size limits** — sync payloads capped at 64 MB / 100K entries (S-03)
- **Schema enforcement on sync** — ontology validation during graph materialization. Invalid entries are accepted into the oplog (preserving CRDT convergence) but quarantined from the materialized graph (R-02). `get_quarantined()` exposes quarantined entry hashes.
- **Value depth limits** — nested structures capped at 64 levels (S-10)
- **Value size limits** — strings capped at 1 MB, lists/maps at 10K items (S-12)
- **File permissions** — redb databases created with 0600 on Unix (S-09)
- **Source name validation** — ObservationLog rejects names > 65535 bytes (S-13)
- **Zero unsafe blocks** — entire Rust codebase

### What's NOT protected (known limitations)

- **Author authentication** — ed25519 signing is implemented (D-027: `generate_signing_key()`, `register_trusted_author()`, `set_require_signatures()`). In default mode, unsigned entries are accepted for backward compatibility. Enable strict mode for full enforcement. Key revocation and rotation are not yet supported.
- **Resource exhaustion** — mitigated via epoch compaction (R-08). `store.compact()` compresses the oplog when all peers have converged. Unbounded growth prevented by periodic compaction.
- **Open network deployment** — Silk is not currently safe for untrusted/open peer networks. Use it between devices and services you control.

## Reporting Vulnerabilities

Report security issues via [GitHub Security Advisories](https://github.com/Kieleth/silk-graph/security/advisories).

Do not open public issues for security vulnerabilities.

## Hardening Roadmap

| Version | What |
|---------|------|
| v0.1 | Clock overflow, bloom validation, message limits, value limits, file permissions |
| v0.2 | HLC clocks (R-01), sync quarantine (R-02), ed25519 signatures (D-027), clock drift bounds |
| v0.3 | Monotonic ontology evolution (R-03), configurable oplog limits |
