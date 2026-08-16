---
name: silk-internals
description: Use when modifying the silk-graph library itself — editing its Rust core (src/*.rs), the PyO3 bindings, the ontology or graph or sync layers, adding a GraphOp or an ontology field, changing serialization, cutting a release, or reasoning about its invariants and PROOF.md claims. For USING silk in an application, use the silk-graph skill instead.
---

# Changing silk

You are working on the engine, not with it. Read `silk-graph`'s references for
the user-facing model; this covers what maintaining it requires.

## Map

| File | Owns |
|---|---|
| `src/entry.rs` | `Entry`, `GraphOp`, `Value`, hashing, serialization |
| `src/oplog.rs` | The DAG: append, heads, topological sort, checkpoint replacement |
| `src/graph.rs` | `MaterializedGraph`: apply/rebuild, quarantine, CRDT merge rules |
| `src/ontology.rs` | Schema types, validators, fingerprint, monotonic extension |
| `src/sync.rs` | `SyncOffer`/`SyncPayload`/`Snapshot`, bloom, `PROTOCOL_VERSION` |
| `src/store.rs` | redb persistence, flush modes, disk reclamation |
| `src/python/` | PyO3 bindings — the only place Python types appear |
| `pytests/` | Behavioral suite (the larger one); `experiments/` holds EXP-* studies |
| `formal/` | TLA+ specs; `scripts/audit_claims.py` cross-references them |

## The law: serialization is positional

**Entries and ontology extensions serialize as positional msgpack arrays with
no field names.** The field count and order ARE the wire format and the
on-disk format.

Adding or removing a field on any serialized struct — however dead it looks —
breaks every persisted store and every peer on an older build. This has bitten
twice in one week, both times disguised as a cleanup.

Two guard tests pin it: `entry_wire_format_is_a_positional_array_of_seven` and
`extension_wire_format_is_a_positional_array_of_four`. If one fails, that is
the alarm working, not a nuisance to update.

When a field genuinely must change:

1. Append at the END, never in the middle.
2. Write a deserialization shim accepting the old arity, and **consume** any
   trailing legacy element — these are read as `Vec<Entry>`, so a stray
   element corrupts every entry after it.
3. Test against bytes produced by the *previous release*, not by round-tripping
   the current struct. A round-trip test proves nothing about old data.
4. Bump `PROTOCOL_VERSION` in `src/sync.rs` so old peers refuse the offer
   cleanly instead of mis-parsing.
5. Say plainly in the CHANGELOG that compatibility is one-directional.

## Materialization

`apply_entry` handles one entry incrementally. `rebuild` is a **two-pass**
replay and the ordering is load-bearing:

- **Pass 1** folds the effective ontology: reset to `base_ontology`, then
  apply each `ExtendOntology` and each checkpoint's inner `DefineOntology` in
  topological order.
- **Pass 2** materializes everything else against that **final** ontology.

Why it must be two passes: entries are judged against the final schema, so an
entry that arrived *before* the extension that rescues it becomes visible.
Judging each entry against the schema as of its own position would
re-quarantine it on every replay, forever — which is the documented
un-quarantine promise, broken. And resetting to `base_ontology` is what makes
replay idempotent: without it, an extension already folded in is re-merged,
fails as a duplicate, and quarantines the store's own schema entry.

`ValidationMode::SkipRequired` exists for exactly one reason: checkpoint inner
ops carry `AddNode` with an empty property map by design (per-property clocks
ride in separate `UpdateProperty` ops), so required-presence cannot be
enforced there. Everything else — declared types, constraints, edge endpoints
— still is. Do not widen this bypass.

Quarantine is a `HashMap<Hash, QuarantineRecord>`: insert on failure, **remove
on every success path**. It must be a function of (base ontology, oplog), not
of sync history.

## Invariants that constrain any change

- **Convergence** — two peers with the same entry set materialize the same
  graph. Anything order-dependent that is not the deterministic topological
  order violates this.
- **Determinism** — `topo_sort` breaks ties by `(physical_ms, logical, hash)`.
  `BTreeMap` everywhere in ontology types so serialization is byte-stable.
- **Monotonic schema** — `OntologyExtension` must never gain a field that
  could narrow anything. The illegal state stays unrepresentable.
- **Never drop a peer's entry** — invalid entries are quarantined, not
  discarded. Dropping breaks convergence.
- **Compaction reproduces the graph exactly** — a compacted store must
  materialize identically to the uncompacted one.

`PROOF.md` states them formally, `INVARIANTS.md` names the mechanical checks,
`formal/*.tla` model-checks the eligible ones. `scripts/audit_claims.py`
fails CI when a claim has no verification surface, and when a CHANGELOG bug
row cites a test identifier that does not resolve.

**If you state an invariant, put its premises in the statement, not only in
the proof.** A claim whose proof carries an unstated premise gets tested at
the weaker statement.

## Working discipline

- **Failing test first.** Every fix lands with the test that proves the bug,
  and the test must fail before the fix. This repo's history is full of bugs
  that survived a green suite because nobody wrote the test.
- **`make check` before pushing** — fmt, clippy `-D warnings`, cargo test,
  maturin build, pytest. A pre-push hook runs it again.
- Add tests to the existing suites, never throwaway scripts.
- Reproduce a reported bug yourself before fixing it. Reports are often right
  about the symptom and wrong about the class.
- A behavior change that makes previously-accepted input an error needs the
  old test updated deliberately, with the reason in the docstring.

## Release

Version lives in **four** places: `Cargo.toml`, `pyproject.toml`,
`python/silk/__init__.py` (`__version__`), and the CHANGELOG heading.
`test_version_consistency` and `pytests/test_version.py` enforce it.

```
make check && git tag vX.Y.Z && git push origin main vX.Y.Z
```

The tag triggers wheels for four targets plus sdist, PyPI, crates.io, and a
GitHub release. Verify all three registries afterwards; the last three
releases each had a publish-stage surprise.

## Deeper

| File | When |
|---|---|
| `references/bug-history.md` | Before changing compaction, quarantine, or serialization — the classes that have recurred and what closed each |
