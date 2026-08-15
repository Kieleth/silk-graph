# Ordo Malleus: Inquisition of silk-graph

Date: 2026-08-13. Inquisitor: malleus-inquisitor skill, rubric v3.
Subject: silk-graph v0.2.7 at `cdfb83b` (working tree clean).
Mechanical rites verdict: **NOT APPLICABLE** (no LinkML schema; see "Mechanical rites" below).
Judgment rites verdict: **7 heresies, 10 suspicions, 3 notes, 6 commendations.**

---

## Cleansing status — 2026-08-14, v0.3.0

Acolyte pass by the project's own session. **Every heresy and every suspicion
is healed.** Each finding was reproduced independently before being touched,
each fix landed with the test its acceptance criterion names, and the
reproduction script was re-run afterwards against the same probes.

| Finding | Status | Where |
|---|---|---|
| H1 foreign checkpoint deletes history | **healed** | `src/python/mod.rs` merge filter; refused unless the oplog is genesis-only |
| H2 remote `UpdateProperty` unvalidated | **healed** | `src/graph.rs` UpdateProperty arm, nodes and edges; `validate_edge_property_update` is new |
| H3 fingerprint blind to 7 of 8 constraints | **healed** | `src/ontology.rs` `fingerprint`/`fingerprint_constraints`; `(true,true)` arm deleted |
| H4 compaction discards quarantined entries | **healed** | `compact()` carries them, re-rooted at the checkpoint |
| H5 local extend never re-evaluates quarantine | **healed** | shared `revalidate()`, also public |
| H6 quarantined *and* materialized | **healed** | removal on every success path |
| H7 unresolvable CHANGELOG citations | **healed** | tests written; `scripts/audit_claims.py` now fails on unresolvable citations |
| S1 rejection reason discarded | **healed** | `get_quarantined_details()` |
| S2 `trusted` too coarse | **healed** | `ValidationMode::SkipRequired` |
| S3 unknown constraint names inert | **healed** | rejected at both ontology entry points; `x_` opt-out |
| S4 compatibility API dead scaffolding | **healed** | documented advisory; `Entry.ontology_hash` removed with a both-arities deserialization shim |
| S5 doc/code contradictions | **healed** | stub, FAQ, PROOF line citation |
| S6 I-06 test weaker than the theorem | **healed** | set equality asserted; premise added to the claim |
| S7 buffer/direct API disagree | **healed** | one validator for both doors |
| S8 `open`/`from_snapshot` skip `validate_self` | **healed** | gated in `extract_ontology_from_genesis`, where all paths meet |
| S9 unvalidated edges on a rebuild that may not come | **healed** | pending-edge set, re-evaluated on endpoint arrival |
| S10 no fingerprint version | **healed** | `fingerprint_version:2` |

### Three corrections to the report

1. **H5's disk claim.** The report records `[D2-disk] after reopen -> sees x1: False`. On reproduction, reopening from disk *did* recover the node, because `open()` replays the persisted `ExtendOntology`. The finding stands — there was no in-process recovery and no public API — but a process restart was a (undocumented, unusable-in-a-loop) way out.

2. **S6 needed more than H6.** The report expected `sorted(a) == sorted(b)` to pass once H6 was fixed. It did not: `rebuild()` did not reset the ontology, so the effective schema depended on how the process was constructed and how many rebuilds had run, not on the oplog. I-06's proof already assumed "the same evolved ontology" while the invariant omitted the premise. Fixed on both sides — rebuild now folds from the base, and the claim states its premise.

3. **S4's deletion was not a cleanup.** `Entry` serializes as a positional msgpack array with no field names, so removing the never-populated field changes the arity of every entry ever written. Verified against real 0.2.7 redb stores. Shipped with a deserialization shim accepting both arities, plus a test pinning the arity so the next such "cleanup" has to be deliberate. `PROTOCOL_VERSION` is 2; this build reads what older builds wrote, but not the reverse.

### Deliberately not done

- **N2 (strict mode for D-026 open properties)** remains a documented divergence, not a defect. No flag was added; that is a product decision, not an acolyte one.
- **N3 (`DefineLens`, `QueryEngine` with no implementations)** left reserved. Both are honest about being reserved, and `DefineLens` ships in the wire format precisely so adding it later is not a break.
- **S4's other half (wiring compatibility into `SyncOffer`)** stays deferred by decision: it is a second protocol change on top of this one, and the behavioral model (merge, quarantine, rebuild) is the design, not a stopgap.

Eight of these findings (H1-H6, S3, S7) were **verified by execution**
against the built `silk` 0.2.7 extension module, not inferred from reading.
They are marked
`[EXECUTED]` and each carries the observed output.

**The full test suite passes while every heresy below is live: 445 passed in
14.92s.** That is the point of an inquisition. Green tests are not evidence
of absence; they are evidence that nobody wrote the test.

---

## Heresies

### H1. A peer's routine compaction silently deletes the receiver's unsynced history  [rubric: flag_never_delete]

**Where:** `src/oplog.rs:56-62`. Any incoming entry with `next == []` and a
`Checkpoint` payload calls `replace_with_checkpoint` and returns `Ok(true)`.
Unconditionally. No comparison against local state, no check that the local
oplog is an ancestor of what the checkpoint represents, no authority
requirement. Reachable over the wire through `merge_sync_payload`
(`src/python/mod.rs:1530-1536`).

**[EXECUTED]** Two peers, mutually registered, fully synced, `record_sync`
called on both:

```
fully synced. X len 3 Y len 3
Y writes y_new (not yet synced). Y sees y_new: True
X compaction guard says: (True, [])
X compacted. now sync X->Y
merged: 1
Y len: 1 | Y sees y_new: False | Y sees x1: True
Y integrity ok: True
```

Y's oplog went from 3 entries to 1. `y_new` is gone from the graph and from
the log. `verify_integrity()` returns ok. Nothing was quarantined, nothing
was logged, no exception was raised.

**Why it matters:** `verify_compaction_safe` (`src/python/mod.rs:1086-1113`)
guards the *compacting* peer. It checks registered peer sync times and
subscriber cursors. It is structurally incapable of protecting the
*receiving* peer, because X cannot observe writes Y has not sent yet. The
guard is not weak, it is pointed at the wrong side of the wire. This also
breaks PROOF Theorem 3: bidirectional sync does converge here, by deletion.

**Fix:** A checkpoint from a foreign author must never replace a local
oplog. Either (a) reject `Checkpoint` entries whose author is not
`self.instance_id` at the merge filter (`src/python/mod.rs:1462-1516`,
alongside the clock-drift and signature checks), or (b) accept it only when
the local heads are reachable from the checkpoint's `compacted_at` frontier,
and quarantine it otherwise. Option (a) is the smaller change and matches
the documented model, where compaction is a local storage decision.

**Done when:** a test constructs the exact scenario above (peer A compacts,
peer B holds an unsynced write, A syncs to B) and asserts
`b.get_node("y_new") is not None` and `b.len() >= 3` after the merge. Add a
second test asserting that a foreign checkpoint arriving at a non-empty
oplog either raises or lands in `get_quarantined()`.

---

### H2. Remote `UpdateProperty` is never validated against the ontology  [rubric: gate_integrity]

**Where:** `src/graph.rs:209-215`. The `GraphOp::UpdateProperty` arm of
`apply_entry` calls `apply_update_property` directly. No validator, no
`trusted` check, no quarantine branch. The local path validates at
`src/python/mod.rs:1432` via `validate_property_update`; the merge path
delegates to `graph.apply` (`src/python/mod.rs:1458`, comment: "Ontology
validation moved to graph.apply()"), and `graph.apply` does not have it.

**[EXECUTED]** Peer A declares `cpu: int` with no bound. Peer B declares the
same slot with `max: 8`.

```
merged: 3 | B quarantined: 0
B node n1: {'cpu': 50}
=> B materialized cpu=50 although its own validator forbids it: True
B local update cpu=50 REJECTED: 's'.'cpu' violates constraint 'max': value 50 exceeds maximum 8
```

And with a declared type mismatch (A says `string`, D says `int`):

```
merged: 3 | D quarantined: 0
D node m1 properties: {'cpu': 'not-a-number'}
```

A string now sits in a slot the local ontology declares `int`. Quarantine
count zero.

**Why it matters:** This is the v0.1.6 bug (`UpdateProperty` bypassed
validation) reopened on the sync side, and INV-4 exists specifically to
prevent it. INV-4's check
(`pytests/test_invariants.py::test_all_graph_ops_have_validation_path`) is a
hand-maintained list of variant names, so it certifies `UpdateProperty` as
"has a validation path" on the strength of the local path alone. A typed
graph that accepts an untyped value from a peer is an untyped graph with
extra ceremony. Note this is *not* covered by D-026 open properties: the
slot is declared, and the value violates its declared type.

**Fix:** add the validator call to the `UpdateProperty` arm of
`apply_entry`, gated on `!trusted`, quarantining on error, exactly as the
`AddNode` arm does at `src/graph.rs:152-159`. The entity's type comes from
`self.nodes.get(entity_id)`. Also extend the caller at
`src/python/mod.rs:1429` to cover edges: it currently uses `get_node` only,
so edge property updates skip `validate_property_update` on every path.

**Done when:** INV-4's check is derived from the code rather than a list.
Concretely: a test that, for each `GraphOp` variant carrying data, merges a
peer entry that violates the local ontology and asserts the entry appears in
`get_quarantined()` and does not appear in the graph. It must fail today for
`UpdateProperty` and pass after the fix.

---

### H3. The ontology fingerprint emits facts for 1 of 8 enforced constraints, so mutually-quarantining peers read "identical"  [rubric: fingerprint_from_validator]

**Where:** `src/ontology.rs:205-222`. `fingerprint_constraints` looks up
exactly one key, `"enum"`. The validator
(`validate_constraints`, `src/ontology.rs:812-918`) enforces eight: `enum`,
`min`, `max`, `min_exclusive`, `max_exclusive`, `min_length`, `max_length`,
`pattern`. Separately, `src/ontology.rs:170-178` fingerprints edge types by
name, source, and target only; `EdgeTypeDef.properties` produces no facts at
all.

**[EXECUTED]** Two peers, `server.cpu` bounded at `max: 100` versus
`max: 8`, then a full bidirectional sync:

```
verdict           : identical
A integrity ok    : True | B integrity ok: True
oplog lens A/B    : 3 3
heads equal       : True
A sees n1 / B sees: True False
A q / B q         : 0 1
```

Identical oplogs, identical heads, both integrity checks green, and the two
peers disagree about whether the node exists. The compatibility check, whose
entire job is to warn about exactly this, returns a clean bill of health.
Same result for `pattern` divergence and for edge-type property divergence:

```
[D1c] edge-prop hash equal: False
[D1c] edge-prop fingerprint equal: True
[D1c] verdict: identical
```

**This is reachable from the ordinary monotonic path, not only from divergent
genesis.** Two peers sharing a genesis, each calling `extend_ontology` to add
the same property name with different bounds:

```
A fp: ['prop:s:cpu:int:optional', 'type:s']
hash eq: False
verdict A vs B: identical
merged: 3 | B quarantined: 3 | B sees n1: False
```

That is the normal federation case: two teams extend the shared root with
the same slot and different bounds.

**Why it matters:** `src/ontology.rs:200` reads
`(true, true) => Compatibility::Identical, // same facts, different hash (shouldn't happen)`.
It happens on every constraint the emitter does not cover. The code
documents its own blind spot as impossible, and that arm is precisely the
one this bug fires through.

**Fix:** the pattern is in `malleus` (`src/malleus/ontology.py`, committed
2026-08-12). Three moves, all of which apply here:

1. Emit a fact for **every enforced constraint**, generated by walking the
   constraint structure the validator consults rather than a hand-written
   parallel list (`_constraint_facts`, `ontology.py:790-804`). In silk that
   means iterating `prop_def.constraints` keys, not `.get("enum")`.
2. Emit **membership facts from the resolved table**, not the declaration
   syntax (`effective_slots`, used at `ontology.py:775-776`), so a slot
   attached by any route (parent type, subtype, extension) is a fact. Silk's
   `parent_type` inheritance is the same hazard.
3. Put a **version fact in the fingerprint itself**
   (`fingerprint_version:{N}`, `ontology.py:754`), so that fixing this does
   not make every upgraded peer read "divergent" against every old one with
   no way to tell a formula change from a real fork. See S10.

Also delete the `(true, true)` arm: order the checks superset-then-subset as
`malleus`'s `check_compatibility` does (`ontology.py:829-841`), so
"shouldn't happen" is unrepresentable rather than silently mapped to the
safest-sounding verdict.

**Done when:** a property-based test asserts, for every constraint name the
validator enforces, that two ontologies differing only in that constraint
produce different fingerprints and a `divergent` verdict. Mechanically: the
test enumerates the constraint names from a single shared constant that
`validate_constraints` also consumes, so adding a ninth constraint without
fingerprinting it fails the build. Same test for edge-type properties.

---

### H4. Compaction discards quarantined entries, voiding the un-quarantine promise  [rubric: flag_never_delete]

**Where:** `build_checkpoint_ops` (`src/python/mod.rs:1332`) reconstructs ops
from the **materialized graph** only. Quarantined entries are by definition
not materialized, so they are absent from the checkpoint.
`compact` (`src/python/mod.rs:1280-1323`) then calls
`replace_with_checkpoint`, dropping the originals.
`verify_compaction_safe` (`src/python/mod.rs:1086-1113`) never looks at
`self.graph.quarantined`.

**[EXECUTED]**

```
before compact: entry resolvable: True | oplog len: 3
verify_compaction_safe: (True, [])
after compact : entry resolvable: False | oplog len: 1 | still reported quarantined: True
integrity ok: True
```

After compaction, `get_quarantined()` returns a hash that `get(hash)`
resolves to `None`. A dangling diagnosis pointer to an entry that no longer
exists.

**Why it matters:** the contract is stated three times, and this breaks all
three. `src/graph.rs:96-97`: "They remain in the oplog for CRDT
convergence." `python/silk/__init__.pyi:368-369`: "Quarantined entries are in
the oplog (for CRDT convergence)." `FAQ.md:574-581`: the entry "is
un-quarantined automatically" when the ontology catches up. For any peer
that compacted, none of that is true. The data is recoverable only if some
other peer still holds the raw entry, and if every peer that quarantined it
compacts, it is gone from the system permanently. A validation failure has
become a delete instruction, which is the exact shape `flag_never_delete`
was written for.

**Fix:** either carry quarantined entries verbatim into the checkpoint
alongside the synthetic ops, or make `verify_compaction_safe` return
`(False, ["N quarantined entries would be discarded"])` when the quarantine
set is non-empty, forcing `safe=False` and an explicit operator decision.
The first preserves convergence; the second at least makes the loss a
choice. Do not do neither.

**Done when:** a test quarantines an entry, compacts, extends the ontology to
accept it, and asserts the entry is visible. Failing that design decision, a
test asserts `verify_compaction_safe()[0] is False` while
`len(get_quarantined()) > 0`. Additionally: assert
`store.get(h) is not None` for every `h in store.get_quarantined()`, as an
invariant, at every point in the suite.

---

### H5. A local `extend_ontology` never re-evaluates quarantine, and there is no public rebuild  [rubric: no rite covers this; proposed upstream]

**Where:** `extend_ontology` (`src/python/mod.rs:366-388`) validates
monotonicity, appends the op, merges into `self.ontology`, and returns. It
never calls `self.graph.rebuild`. The only rebuild trigger is the inbound
merge path (`src/python/mod.rs:1543-1557`), gated on `has_schema_change`
within the arriving batch. `rebuild` itself (`src/graph.rs:236-244`) is not
exposed to Python: no `rebuild`, no `revalidate`, no `unquarantine`.

**[EXECUTED]** Peer Y quarantines a node whose type it does not know. The
operator performs the exactly correct remediation:

```
step1 Y quarantined: 1 sees x1: False
step2 after local extend  q: 1 sees x1: False
[D2] Y CAN now write newtype locally: True
[D2] re-sync merged: 0 | quarantined: 1 | sees x1: False
[D2] public escape hatch: []
```

Y can now write that type itself, and still cannot see the peer's node.
Reopening the store from disk does not help either:

```
[D2-disk] before reopen -> quarantined: 1 | sees x1: False
[D2-disk] after reopen  -> quarantined: 1 | sees x1: False
```

**Why it matters:** `FAQ.md:574-581` documents the four-step un-quarantine
mechanism as automatic. It is automatic on one of the two paths that change
the ontology. An operator who reads the docs, diagnoses the mismatch
correctly, and applies the correct fix is left with permanently invisible
data and no API to recover it. The failure mode is worse than a crash,
because the system reports success at every step.

**Fix:** call `self.graph.rebuild(&all_entries)` at the end of
`extend_ontology`, mirroring `src/python/mod.rs:1556-1575` including the
un-quarantine subscriber notification. Separately, expose a public
`revalidate()` so the recovery does not depend on having guessed the right
trigger.

**Done when:** the test from H4's criterion passes through the *local*
extension path as well as the sync path, plus a test asserting
`hasattr(store, "revalidate")` and that calling it on a store with a stale
quarantine set clears it.

**Upstream:** rubric v3 has no rite for "the documented remediation does not
work". Proposed generic lesson, no project details: *a state that a
validation gate produces must have exactly one documented exit, and that
exit must be reachable from the operator-facing API. A remediation path that
works on one of two equivalent trigger paths is worse than none, because it
converts a diagnosable failure into a silent one.*

---

### H6. `get_quarantined()` can report an entry that is materialized and visible  [rubric: rejection_as_data, elevated]

**Where:** `apply_entry` inserts into `self.quarantined` on failure
(`src/graph.rs:140, 157, 180, 195`) but never removes on success
(`src/graph.rs:161, 200`). Only `rebuild` clears the set
(`src/graph.rs:242`). Meanwhile `candidate_hashes`
(`src/python/mod.rs:1527`) is built from every entry in the payload, not
only the newly inserted ones, so the incremental branch
(`src/python/mod.rs:1578-1593`) re-applies entries the peer resent. A
previously quarantined entry re-applied under an evolved ontology
materializes and keeps its quarantine record.

**[EXECUTED]**

```
step3 after sync(new entry) merged: 1 q: 1 sees x1: True sees x2: True
      the hash still reported quarantined is: {"op":"add_node","node_id":"x1",...}
      => reported quarantined AND materialized: True
```

**Why it matters:** PROOF I-06 (`PROOF.md:138-140`, restated at `:227`)
claims two peers with identical oplogs produce identical quarantine sets. On
the incremental path the set is not a function of the oplog at all, it is a
function of sync history: whether some later payload happened to re-include
the entry. Two converged peers can report different quarantine sets. And the
operator-facing signal is now wrong in both directions: H5 shows an entry
that is invisible and cannot be recovered, this shows an entry that is
visible and still flagged.

**Fix:** in `apply_entry`, `self.quarantined.remove(&entry.hash)` on every
success path. Cheap, and it makes "quarantined implies not materialized" a
real invariant rather than an aspiration.

**Done when:** an assertion added to the shared test helpers, run after every
merge in the suite: `set(store.get_quarantined())` and the set of
materialized entry hashes are disjoint. Plus a direct test of the sequence
above.

---

### H7. The CHANGELOG cites a verification artifact that does not exist, and the un-quarantine mechanism has zero tests in any language  [rubric: citation_integrity]

**Where:** `CHANGELOG.md:19` cites, as the verification for the 0.1.7
notification bug, an "Ontology-extension notification integration test in
`src/python/mod.rs` merge path". `src/python/mod.rs` contains zero `#[test]`
and zero `#[cfg(test)]`. No test name is given, so nothing is even
greppable. The code it claims to cover is real
(`src/python/mod.rs:1566-1570`) and untested.

Second citation, `CHANGELOG.md:14` (repeated at `INVARIANTS.md:37`), points
Bug 5 at `pytests/test_invariants.py::test_sync_convergence_randomized`.
That test exists (`pytests/test_invariants.py:161`) and contains zero calls
to `extend_ontology` and zero calls to `get_quarantined()`. It cannot
exercise the bug it is cited for.

Coverage census for the un-quarantine path, all languages:

- `pytests/test_quarantine.py`: 8 tests, zero `extend_ontology` calls. Every
  visibility assertion runs the wrong direction (`is None`, lines 74, 96,
  143, 188).
- `pytests/test_extend_ontology.py`: the two `is not None` tests (lines 224,
  245) send the extension and the node in the *same* sync, so the node is
  never quarantined first. They prove ordering, not re-evaluation.
- `pytests/test_sync.py`: zero `extend_ontology` calls; quarantine
  assertions are count-only.
- `src/graph.rs`: 16 `#[test]` fns, none covering ExtendOntology-arrives-later.
- `src/python/mod.rs`: zero tests.

**Why it matters:** the four-step mechanism at `FAQ.md:574-581` has no test
at any step. That is why H5 and H6 both survived a 445-test suite. A
CHANGELOG citation that does not resolve is the same class of defect as a
rule citing a renamed axiom: it dies silently, and it purchases false
confidence at exactly the moment someone goes looking for reassurance.

**Fix:** write the test. Then make citations mechanical.

**Done when:** `scripts/audit_claims.py` (which already exists and already
walks the docs) is extended to parse the "Verified by" column of the
CHANGELOG table and fail when a cited test identifier does not resolve to a
real `def test_*` or `#[test] fn`. Free-text citations with no identifier
must fail the parse, not pass it.

---

## Suspicions

### S1. Quarantine returns bare hashes; the rich `ValidationError` is thrown away  [rubric: rejection_as_data]

**Where:** all four quarantine sites discard the diagnosis.
`src/graph.rs:138` (`if let Err(_e)`), `:153` (`if let Err(_e)`), `:179`
(no error even constructed, just `contains_key`), `:190-194` (`.is_err()`).
`get_quarantined` (`src/python/mod.rs:1040-1046`) returns `Vec<String>` of
hex.

`ValidationError` (`src/ontology.rs:227-274`) has eleven variants carrying
type name, property, expected type, allowed set, constraint name, and a
message. All of it is dropped one line before the operator needs it.

**Fix:** store `HashMap<Hash, QuarantineRecord>` where the record is
`(op_kind, ValidationError, ontology_hash_at_decision)`, and return dicts
from `get_quarantined()`. `malleus` carries `rejection_reason` on staged
candidates for the same reason.

**Done when:** a test quarantines one entry of each failure kind and asserts
`get_quarantined()[0]["reason"]` contains the offending type name, and that
the reason string differs between an unknown-type rejection and a
constraint violation.

---

### S2. `trusted` is inferred from calling context, never a property of the operation  [rubric: trusted_explicit]

**Where:** `apply_entry(&mut self, entry, trusted: bool)`
(`src/graph.rs:109`). Exactly one site passes `true`: `src/graph.rs:131`,
inside the `Checkpoint` arm, at recursion depth 1. Neither `GraphOp`
(`src/entry.rs:26-84`) nor `Entry` (`src/entry.rs:94+`) has a trust field.
Nothing is signed or content-addressed as trusted.

Two aggravations. First, the trusted branch also swaps the ontology
wholesale: `src/graph.rs:120-123` does `self.ontology = ontology.clone()`
with no `validate_self`, no monotonicity check, no comparison against the
current ontology. Second, per H1, checkpoints arrive over the wire, so the
trusted path is remote-reachable.

The rationale is sound and documented (`src/graph.rs:101-108`, Bug 14b):
`build_checkpoint_ops` emits `AddNode` with `properties: BTreeMap::new()`
(`src/python/mod.rs:1352`, and `:1380` for edges), so re-validation would
fail every required property. But the bypass is far coarser than the
problem. It disables the required-property check, and also every constraint
check, every edge endpoint check, and the ontology swap.

**Fix:** narrow the bypass to what Bug 14b actually needs. Either emit
checkpoint `AddNode` ops with their real properties (removing the need for a
bypass at all), or replace the boolean with an explicit
`ValidationMode::SkipRequiredOnly` so the other checks still run.

**Done when:** a test asserts that a checkpoint whose inner ops violate a
constraint (not a required-property rule) is quarantined or rejected, and
that a checkpoint's inner `DefineOntology` that is not monotonic against the
current ontology does not silently replace it.

---

### S3. Unknown constraint names are silently ignored, so a typo is inert and invisible  [rubric: single_source]

**Where:** `src/ontology.rs:916-917`. `validate_constraints` does positive
lookups only (`:819, 838, 847, 856, 865, 876, 885, 892`) and never iterates
the supplied keys, so no key is ever compared against a known set.
`validate_self` (`src/ontology.rs:624-656`) checks edge endpoint references
and `parent_type` only; it never descends into `PropertyDef.constraints`.

**[EXECUTED]**

```
accepted cpu=999999 under constraints {'maximum':8}: True
fingerprint: ['prop:s:cpu:int:optional', 'type:s']
```

`maximum` instead of `max`. Accepted at construction, never warned about,
never enforced, and invisible to the fingerprint. The constraint is inert
forever and the operator has no signal.

**Fix:** validate constraint key names against the known set at
construction and at `extend_ontology`, from the same constant
`validate_constraints` consumes. If forward compatibility for
community constraints is wanted, keep it explicit: an
`x_` prefix convention, or a declared `unknown_constraints: allow` flag, so
that silence is a choice rather than a typo.

**Done when:** `GraphStore("i", {...\"maximum\": 8...})` raises, and a test
asserts the error names the offending key. Plus: the known-constraint list
lives in one place that both the validator and the fingerprint import.

---

### S4. The compatibility check is dead scaffolding: nothing on the sync path calls it  [rubric: reader_census]

**Where:** `Ontology::check_compatibility` (`src/ontology.rs:184`) has
exactly two non-test call sites, both the manual Python method
(`src/python/mod.rs:481`, `:493`). `SyncOffer` (`src/sync.rs:22-35`) carries
`protocol_version`, `heads`, `bloom`, `physical_ms`, `logical`, and no
ontology field. `Entry.ontology_hash` (`src/entry.rs:115`) is `None` at both
construction sites (`:148`, `:174`) and is excluded from the content hash by
design (`:739-758`). It is always `None` in production.

Actual peer compatibility is behavioral: blind merge, quarantine, and a
rebuild if an `ExtendOntology` or `Checkpoint` happens to be in the same
batch. That is a defensible design. What is not defensible is shipping a
compatibility API that the system never consults and that, per H3, returns
the wrong answer when a human does consult it.

**Fix:** either wire it (put `ontology_hash` and a fingerprint digest in
`SyncOffer`, log or refuse on `divergent`) or mark it explicitly advisory in
the docstring and the `.pyi`, and delete `Entry.ontology_hash` if it is not
going to be populated.

**Done when:** either a test asserts a `divergent` peer's offer is refused or
logged, or the `.pyi` for `check_ontology_compatibility` says "advisory
only; never consulted during sync" and a test asserts
`Entry.ontology_hash is None` is the documented production state.

---

### S5. Three doc/code contract contradictions  [rubric: single_source]

**a. The type stub claims the quarantine set is grow-only.**
`python/silk/__init__.pyi:370`, verbatim:
`The quarantine set is grow-only — entries never leave.`
Contradicted by `src/graph.rs:242`
(`self.quarantined.clear()`). The stub is the only place in the repo missing
the qualifier; `PROOF.md:140`, `PROOF.md:225`, `PROTOCOL.md:320`,
`ROADMAP.md:94`, and `src/graph.rs:69-74` all say "within a single
materialization pass". Two secondary unqualified instances:
`CHANGELOG.md:162` and `pytests/test_quarantine.py:147`. The stub is also
wrong in a second way per H6: it says quarantined entries are "invisible in
the materialized graph", and they are not always.

**b. The FAQ claims the compatibility check runs automatically during
sync.** `FAQ.md:310`: "# Manual compatibility check (Silk does this
automatically during sync)". `FAQ.md:301` adds that a `divergent` verdict
means "Reject sync." Both are negated 35 lines later at `FAQ.md:345`: "We
don't do this. The hash is informational, and the sync proceeds regardless."
README, DESIGN, and PROTOCOL are clean on this.

**c. Stale line citation.** `PROOF.md:142` cites `src/graph.rs:209` for the
quarantine clear. It is at `:242`.

**Fix:** correct all three. For (b), delete the claim rather than the
negation; the negation is the true one.

**Done when:** `scripts/audit_claims.py` grows a check that every
`src/*.rs:NNN` citation in the docs resolves to a line whose content matches
a required substring, so line drift fails CI. The grow-only phrasing gets a
grep-level ban unless followed by "within a single materialization pass".

---

### S6. The I-06 test asserts strictly weaker than the theorem  [rubric: citation_integrity]

**Where:** `PROOF.md:138-140` and `:227` claim "Two peers with identical
oplogs produce identical quarantine sets". The only test in the repo that
reads both peers' sets is `pytests/test_extend_ontology.py:285-288`:

```python
quarantined_a = store_a.get_quarantined()
quarantined_b = store_b.get_quarantined()
# At least one peer should quarantine the conflicting extension
assert len(quarantined_a) > 0 or len(quarantined_b) > 0
```

A disjunction that passes when the sets are maximally different: A nonempty,
B empty. The sets are never compared, never sorted, never length-checked
against each other. No test anywhere in the repo asserts equality of two
quarantine sets.

The claim-audit pointer is weaker still. `pytests/test_invariants.py:432`
resolves I-06 to `test_quarantine_grows_only`
(`pytests/test_quarantine.py:146-166`), which asserts `q2 >= q1` after
syncing the *same* payload twice with no ontology extension. Vacuously true.

**Why it matters:** per H6 the theorem is currently false on the incremental
path. The test was the one thing that could have caught it, and it was
written to the shape of the observation rather than the shape of the claim.

**Fix:** `assert sorted(quarantined_a) == sorted(quarantined_b)` after
bidirectional sync completes.

**Done when:** that assertion is in the suite and passes, which requires H6
fixed first.

---

### S7. `OperationBuffer.drain` reports success for an operation the direct API rejects loudly  [rubric: silent_drop]

**Where:** `PyOperationBuffer` enqueues without validation
(`src/python/mod.rs:1722, 1748, 1773`, documented at `:1704`) and drains via
`store.append` (`:1655`), which runs `validate_entry_payload` only. That
validator's edge branch (`src/python/mod.rs:1421-1427`) checks that the edge
type exists and stops. `PyGraphStore::add_edge` (`:301`, check at `:322-325`)
additionally validates source and target types.

**[EXECUTED]** Same operation, two doors:

```
direct add_edge (bad target type) REJECTED: edge 'R' cannot have target type 'b' (allowed: ["a"])
drain -> 1
edge e1 materialized (bad target type b): False
quarantined: 1
```

`drain()` returns 1, reporting one operation applied. The edge does not
exist. No exception, no reason, and the operator has to think to call
`get_quarantined()` and then, per S1, gets a bare hash.

**Fix:** run the full `validate_edge` in `validate_entry_payload` so both
doors have the same lock, or make `drain` return
`(applied, rejected_with_reasons)` instead of a count.

**Done when:** a test asserts that every operation kind rejected by the
direct API is also rejected by the buffer path, with the same error type.
Parameterize it over the op kinds so a new op cannot skip the comparison.

---

### S8. `open()` and `from_snapshot()` skip `validate_self()` on the genesis ontology  [rubric: gate_integrity]

**Where:** `GraphStore.open` (`src/python/mod.rs:201`) extracts the ontology
from genesis at `:209` and never calls `validate_self()`. `from_snapshot`
(`:846`, extraction at `:858`) is the same. The constructor
(`src/python/mod.rs:103`, `validate_self` at `:113`) and `extend_ontology`
(`:366`, via `merge_extension` at `src/ontology.rs:754`) both do call it.

Two of four ontology entry points check the ontology's internal consistency.
A store whose genesis has a dangling edge endpoint reference (writable
through a hand-edited or foreign oplog) opens without complaint.

**Fix:** call `validate_self()` on the extracted ontology in both paths and
raise on error.

**Done when:** a test writes a store, corrupts the genesis ontology to
reference a nonexistent node type in `source_types`, and asserts `open()`
raises rather than returning a store.

---

### S9. Edge endpoint validation is skipped when an endpoint is not yet materialized, and the promised rebuild may never come  [rubric: gate_integrity]

**Where:** `src/graph.rs:183-198`. When either endpoint is missing from
`self.nodes`, source/target type validation is skipped entirely. The comment
at `:184-186` says "validation happens on rebuild". But rebuild only runs
when the merge batch contains an `ExtendOntology` or `Checkpoint`
(`src/python/mod.rs:1543-1552`). For out-of-order sync with no schema change
in the batch, no rebuild ever fires, and the edge stays permanently
unvalidated.

**Fix:** defer such edges to a pending set re-evaluated when the missing
endpoint materializes, rather than admitting them and relying on a rebuild
that is not guaranteed.

**Done when:** a test syncs an edge before its endpoints, then the endpoints,
with no ontology change anywhere in the sequence, and asserts an
endpoint-type-violating edge ends up quarantined.

---

### S10. The fingerprint has no version fact, so fixing H3 will read as a fork  [rubric: divergence_signal_is_quiet]

**Where:** `src/ontology.rs:123-181`. Every fact is structural. Nothing
identifies the emitter's own formula version.

The moment H3's fix lands, an upgraded peer's fingerprint is a strict
superset of an old peer's for the same ontology, so `check_compatibility`
reports `superset` or `divergent` where the ontologies are in fact
identical. Operators cannot distinguish a real fork from a formula upgrade,
and the standard response to a divergence check that cries wolf is to stop
reading it.

**Fix:** emit `fingerprint_version:{N}` as a fact and bump it whenever the
emitter changes, exactly as `malleus` does at
`src/malleus/ontology.py:754`. Then a version mismatch is a distinct,
nameable condition instead of a false fork.

**Done when:** a test asserts that two ontologies that are byte-identical but
fingerprinted by different emitter versions produce a verdict that is
neither `identical` nor silently `superset`, and that the version fact is
present in every fingerprint.

---

## Notes

**N1. Mechanical rites: not applicable.** `malleus-inquisitor` takes a LinkML
schema. silk-graph has none, by design: its ontology is a runtime JSON
document supplied at construction and stored as genesis entry #0, not a
static schema file. There is nothing for the mechanical rites to load. What
was run instead is recorded in the section below.

**N2. D-026 open properties is a documented divergence, not a defect.**
Undeclared property names skip validation (`src/ontology.rs:779-783` and
`:596-600`; decision text at `DESIGN.md:1343-1361`, `README.md:218`,
`FAQ.md:136`). "The ontology defines the minimum, not the maximum" is the
opposite of malleus's strict stance, and it is stated, tested, and owned.
Recorded here only because there is no strict mode anywhere in the repo (no
flag, env var, feature gate, or constructor argument), and `DESIGN.md:1361`
punts strictness to the application layer, which means an adopter who needs
it has to build it. Note that H2 is *not* an instance of D-026: there the
slot is declared and the value violates its declared type.

**N3. Two declared-but-unspoken surfaces.** `GraphOp::DefineLens`
(`src/entry.rs:67`) is a reserved no-op in every consumer
(`src/graph.rs:222`, `src/provenance.rs:57`,
`src/python/conversions.rs:322`). `python/silk/query.py`'s `QueryEngine`
protocol has zero implementations and `QUERY_EXTENSIONS.md` carries a
Datalog sketch whose body is `...`. Both are honest about being reserved.
Flagged only under `reader_census`: vocabulary with no reader is where drift
starts, and a reserved variant that ships in the wire format is harder to
change later than one that does not.

---

## Commendations

These are the disciplines worth showcasing. Several are better than what the
inquisitor has seen in projects that pass more rites.

- **The ontology is genesis entry #0 of the Merkle-DAG oplog**, not a side
  table (`src/python/mod.rs:101-197`). On reopen the oplog is authoritative
  and a declared ontology only seeds new stores (`:169`). The schema is
  content-addressed, causally ordered, and replicated by the same machinery
  as the data. This is the single strongest idea in the repo and the one
  most worth copying. Candidate for `docs/RECIPES.md`.

- **Two failure modes, correctly chosen.** Local writes are hard-rejected
  before an entry exists (`src/python/mod.rs:283-286`); entries arriving
  over sync are quarantined at materialization and kept in the oplog
  (`src/graph.rs:95-97`). Refusing to drop foreign entries from the log is
  what makes convergence possible, and the split is the right one. H4 is a
  bug in the implementation of this idea, not a flaw in the idea.

- **Monotonic extension is enforced by the type system, not by discipline.**
  `OntologyExtension` (`src/ontology.rs:344-368`) has three fields and
  `NodeTypeUpdate` has three more, and none of them can narrow anything:
  add types, add edges, add properties, relax required to optional, add
  subtypes. Constraints on an existing property cannot change because there
  is no field that could express it. Verified: attempting to redefine an
  existing type is rejected as `DuplicateNodeType`. Making the illegal state
  unrepresentable beats validating against it.

- **The provenance primitive is deterministic and compaction-aware.**
  `entries_affecting` (`src/provenance.rs`) scans the oplog for every entry
  referencing an id, in topological order, identically on every peer, and
  recurses into `Checkpoint` inner ops so compaction does not erase history.
  PROOF Theorem 5. This is the right shape for a provenance primitive and it
  is genuinely rare.

- **Determinism is engineered, not hoped for.** `BTreeMap` throughout the
  ontology types for stable serialization; INV-2 asserts byte-identical
  serialization across 100 round-trips; `scripts/audit_claims.py` sorts glob
  results because a CI run caught the nondeterminism (`32f5ab6`). The
  instinct to make the test deterministic rather than retry it is correct.

- **The correctness track record is kept in public.** `CHANGELOG.md` carries
  a bug table with the failure, the class, how it was found, and what
  verifies it, and `INVARIANTS.md` names six invariants with named checks,
  and 7/7 eligible PROOF claims have TLA+ specs. H7 is a criticism of two
  rows in that table, not of the practice. The practice is right, and it is
  the reason this inquisition could be specific: a project that documents
  what it verified can be held to it. Most cannot.

---

## Mechanical rites

No LinkML schema exists in this repository, so the mechanical rites have no
subject. Verbatim, for the record:

```
$ malleus-inquisitor --help
usage: malleus-inquisitor [-h] [--map NAME=PATH] [--root ROOT] [--json] schema

Ordo Malleus: mechanical rites over a malleus-derived schema.

positional arguments:
  schema           path to the project's LinkML schema
```

```
$ find . -name '*.yaml' -o -name '*.yml' | grep -v target
./.github/workflows/release.yml
./.github/workflows/ci.yml
./.github/workflows/bench.yml
```

CI workflows only. No schema, and no `malleus` reference anywhere in the
source or docs.

Substituted for the mechanical rites: the full judgment rubric applied to
the Rust ontology layer as a malleus-shaped ontology in another format, plus
executable probes against the built `silk` 0.2.7 extension module. Baseline
run before probing:

```
$ python3 -m pytest pytests/ -q
445 passed in 14.92s
```

Every `[EXECUTED]` finding above was produced against that same module, on
that same commit, with the suite green.
