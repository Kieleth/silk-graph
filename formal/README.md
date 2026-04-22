# Formal Specifications (TLA+) and Claim Audit

Machine-checked verification of Silk's core invariants using TLA+ and the TLC model checker, plus an automated audit that tracks which PROOF.md and INVARIANTS.md claims are covered by a test, a formal spec, or both.

## Claim coverage (current)

Regenerate with `python scripts/audit_claims.py`; snapshot lives in `formal/audit.json` (committed, diffs surface in PRs).

| Layer | Status |
|---|---|
| PROOF.md claims with ≥1 verification surface | 9/9 |
| TLA+-eligible invariants/theorems modeled | 7/7 |
| INVARIANTS.md claims with a test | 6/6 |

Out of TLA+ scope by design:
- **I-01** (hash integrity) — cryptographic, verified by unit test, not structural reasoning.
- **I-06** (quarantine determinism) — corollary of Theorem 3; see PROOF.md §6.

The audit is gated in CI via `pytests/test_claim_audit.py`. Adding a new claim without a verification surface, or a TLA+-eligible claim without a spec reference, fails the suite.

## Specifications

### `OpLog.tla` — Single peer

Models one oplog as a set of entries with parent links. Verifies:

- **I-02 (CausalComplete):** every entry's parents exist in the oplog.
- **I-03 (AppendOnly):** entries set never shrinks across steps (temporal property).
- **I-04 (HeadsAccurate):** `heads` equals the set of entries no other entry references as a parent.

9,569 distinct states explored, depth 5. Zero violations.

### `SilkSync.tla` — Two peers

Models two peers that independently append entries and sync bidirectionally, including a replay action that reapplies a previous sync. Verifies:

- **I-02 (CausalComplete):** both peers, every reachable state.
- **Theorem 1 (Deterministic Materialization):** `MaterializeOf` is a pure function of the entry set; after bidirectional sync both peers compute the same materialized state.
- **Theorem 2 (Idempotent Merge):** `ReplaySyncAtoB` leaves state unchanged when A's entries are already in B.
- **Theorem 3 (Convergence):** after bidirectional sync, entry sets are identical.
- **I-05 (Topo Order Determinism):** captured in miniature via the deterministic `CHOOSE`-based winner selection. TLC enforces the resulting state is unambiguous.

99,494 distinct states explored, depth 8. Zero violations.

## What the audit script does

`scripts/audit_claims.py`:

1. Scans `PROOF.md` for `I-0X` and `Theorem X` identifiers, `INVARIANTS.md` for `INV-X`.
2. For each claim, greps Rust tests (`src/**/*.rs`, `tests/**/*.rs`), Python tests (`pytests/**`), and TLA+ specs (`formal/*.tla`, `formal/*.md`) for references.
3. Writes `formal/audit.json` (committed) and prints a summary to stdout.
4. Exits non-zero if any claim has zero verification surfaces.

A claim "counts" as TLA+-modeled when the identifier appears in a spec file. Ineligible claims are listed in `TLA_INELIGIBLE` at the top of the script with a one-line rationale.

## Running locally

```bash
# TLA+ tools (one-time; formal/tla2tools.jar is gitignored)
curl -sL https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar \
    -o formal/tla2tools.jar

# Model check both specs
cd formal
java -cp tla2tools.jar tlc2.TLC OpLog   -config OpLog.cfg   -workers auto -deadlock
java -cp tla2tools.jar tlc2.TLC SilkSync -config SilkSync.cfg -workers auto -deadlock

# Run the claim audit (regenerates audit.json)
cd ..
python scripts/audit_claims.py

# Run the gate in pytest
python -m pytest pytests/test_claim_audit.py -v
```

## Scope

These specifications model the **entry-set and materialization layers** of Silk. They deliberately do NOT model:

- BLAKE3 hash correctness (verified by unit test)
- HLC clock ordering details (the TLA+ tiebreaker is `CHOOSE`, which is deterministic on identical sets)
- Bloom filter false-positive handling (the spec models sync as an idealized set union)
- redb persistence semantics
- Schema validation and quarantine application order

The value: TLC exhaustively enumerates every interleaving of writes, syncs, and replays within the bound (5 hashes single-peer, 4 hashes two-peer). If a counterexample exists within that bound, TLC finds it. For properties outside this layer, the defense is unit/integration/property tests. The audit script makes the coverage surface explicit so we can see where each claim lives.

## Adding a new claim

1. Write the claim in PROOF.md or INVARIANTS.md with a stable identifier (`I-0X`, `Theorem X`, or `INV-X`).
2. Add at least one test that references the identifier by name. If the claim is structural and TLA+-eligible, extend a spec in `formal/` or add a new `.tla` file that mentions the identifier in a comment.
3. Run `python scripts/audit_claims.py`, commit the updated `formal/audit.json`.
4. `pytest pytests/test_claim_audit.py` must pass.
5. If the claim is invariant/theorem-kind but cannot be modeled in TLA+, add it to `TLA_INELIGIBLE` in `scripts/audit_claims.py` with a one-line rationale.
