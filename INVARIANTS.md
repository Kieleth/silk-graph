# Silk Invariants

Structural properties that must **always** hold. Each has an automated check that runs in CI or at startup. If an invariant is violated, the check fails loudly — no silent corruption.

> See [PROOF.md](PROOF.md) for the formal convergence theorems (I-01 through I-06, Theorems 1–3).
> This document covers the **automated enforcement** layer above the proofs.

---

## INV-1: Every PROOF.md invariant has a test

**Rule:** Every invariant (I-01–I-06) and theorem (1–3) documented in PROOF.md must have at least one test that references it by name. If a new invariant is added to the proof without a corresponding test, the check fails.

**Why:** Proofs on paper are worthless if the code can silently diverge. The test suite is the executable proof.

**Check:** `pytests/test_invariants.py::test_proof_coverage` — parses PROOF.md for invariant/theorem identifiers, greps the test suite for references. Fails if any identifier is untested.

---

## INV-2: Serialization is deterministic

**Rule:** Identical entry payloads must produce identical bytes across serialization round-trips. This is the foundation of content addressing — if serialization is non-deterministic, two peers computing the same entry get different hashes.

**Why:** Silk uses `BLAKE3(msgpack(payload))` as the content address. BTreeMap ordering + MessagePack must be stable. A serde or msgpack update that changes field ordering would silently break deduplication and sync.

**Check:** `pytests/test_invariants.py::test_serialization_determinism` — creates a known entry, serializes it 100 times, asserts all byte sequences are identical. Also round-trips through `to_bytes()`/`from_bytes()` and verifies hash stability.

---

## INV-3: Bidirectional sync converges for random entry orderings

**Rule:** For any two peers with any sequence of writes, bidirectional sync must produce identical materialized graphs. This is Theorem 3 — but tested with randomized inputs, not just hand-crafted scenarios.

**Why:** Hand-crafted sync tests cover known edge cases. Randomized tests catch ordering-dependent bugs that humans don't think of. The UpdateProperty validation bypass (v0.1.6) was this class of bug.

**Check:** `pytests/test_invariants.py::test_sync_convergence_randomized` — generates random node/edge/property sequences on two independent stores, syncs bidirectionally, asserts identical graph state. Runs 20 random scenarios per CI invocation.

---

## INV-4: Every write path validates against the ontology

**Rule:** Every `GraphOp` variant that touches data (`AddNode`, `AddEdge`, `UpdateProperty`) must have a validation path in `validate_entry_payload()`. Variants that don't need validation (`RemoveNode`, `RemoveEdge`, `DefineOntology`, `ExtendOntology`, `Checkpoint`) must be explicitly listed.

**Why:** The v0.1.6 bug — `UpdateProperty` silently bypassed ontology validation. When a new `GraphOp` variant is added, the developer must decide: validate or explicitly skip. The wildcard `_ => Ok(())` should not silently absorb new variants.

**Check:** `pytests/test_invariants.py::test_all_graph_ops_have_validation_path` — enumerates all `GraphOp` variants (via a known list maintained in the test), asserts each is either validated or explicitly documented as not needing validation. If a new variant appears without being added to either list, the test fails.

---

## INV-5: Version consistency across Cargo.toml, pyproject.toml, and CHANGELOG

**Rule:** The version in `Cargo.toml`, `pyproject.toml`, and the latest `## [x.y.z]` entry in `CHANGELOG.md` must all match. No release goes out with mismatched versions.

**Why:** Manual version bumps across three files are error-prone. A mismatch means crates.io and PyPI publish different versions, or the changelog doesn't document what shipped.

**Check:** `pytests/test_invariants.py::test_version_consistency` — reads all three files, extracts versions, asserts equality.

---

## INV-6: Oplog integrity is verifiable

**Rule:** At any point, the full oplog can be verified: every entry's hash is valid (I-01), every entry's parents exist (I-02), and heads are accurate (I-04). A corrupted or tampered store is detectable.

**Why:** Disk corruption, buggy serialization, or a malicious peer could introduce entries with invalid hashes or broken parent links. A full integrity check catches this before the corrupted data propagates via sync.

**Check:** `store.verify_integrity()` — Python method that walks the full DAG, re-hashes every entry, verifies parent existence, and checks head accuracy. Returns `(ok: bool, errors: list[str])`. Also tested in `pytests/test_invariants.py::test_verify_integrity`.

---

## Running the checks

```bash
# All invariant checks
python -m pytest pytests/test_invariants.py -v

# Full test suite (includes invariant checks)
python -m pytest pytests/ -v
```

When CI is configured, these run on every push and PR.
