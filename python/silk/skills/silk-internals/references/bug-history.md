# Bug history: the classes that recur

Not a changelog. These are the failure *shapes* this codebase produces, so the
next one gets caught before it ships. Every one below shipped under a green
test suite.

## 1. Compaction breaks replay, silently

**Bug 14 (0.2.6).** Compaction folds every `ExtendOntology` into the
checkpoint's inner `DefineOntology`, but replay skipped `DefineOntology` ops.
The merged ontology was never restored, so every extension-typed entity
quarantined on reopen. A production store came up near-empty with the data
sitting intact on disk.

**Bug 14b (0.2.7).** The residual. Replay validated the checkpoint's synthetic
inner ops, whose `AddNode` carries an empty property map by design — so every
entity with a **required property** quarantined. Reported downstream as a
subtype problem; subtypes were incidental, they just usually carry required
properties.

**The class:** compaction produces ops that its own replay refuses. Any change
to `build_checkpoint_ops` or to the `Checkpoint` arm of `apply_entry` must be
tested by *reopening a compacted store*, through both `GraphStore.open()` and
the constructor, with an extended ontology and required properties present.

**What let both through:** the compaction tests never closed and reopened the
store, and no test ontology marked a property required.

## 2. Validation added on one path, missing on the other

**0.1.6.** `update_property` bypassed ontology validation locally.

**H2 (0.3.0).** The same bug reopened on the **sync** side: the merge path
delegated to `graph.apply`, whose `UpdateProperty` arm had no validator at
all. A peer's value entered the graph regardless of local type or constraints.
Edge property updates had no validator on *any* path.

**The class:** local writes and merged entries are different code paths, and a
rule added to one is not on the other. There are also two doors for writes —
the direct API and `OperationBuffer.drain` — which had different edge rules
(S7).

**Guard:** for each `GraphOp` variant carrying data, a test that merges a
peer entry violating the local ontology and asserts it quarantines.

## 3. A gate that observes but cannot act

**H5 (0.3.0).** `extend_ontology` never re-evaluated quarantine, so the
documented un-quarantine mechanism worked on the sync trigger only. An
operator who diagnosed a schema mismatch correctly and applied the correct fix
was left with permanently invisible data and no API to recover it — every step
reporting success.

**The class:** a state a validation gate produces needs exactly one documented
exit, reachable from the public API, and it must work from *every* path that
can change the precondition. Now: one shared `revalidate()`, also public.

## 4. Silent no-op reported as success

**0.4.0.** `extend_ontology` accepted any payload whose keys it did not
recognize — serde ignores unknown fields, so it deserialized to an empty
extension. The call returned a hash, appended an entry to the replicated
oplog, and changed nothing. Downstream this read as "the ontology cannot be
mutated": the migration reported success, the schema never moved, and the next
boot's declared-vs-live check failed again. It cost an outage.

Same shape one layer down: **S3 (0.3.0)**, an unknown constraint name like
`maximum` for `max` was accepted, never enforced, and invisible to the
fingerprint — inert forever with no signal.

**The class:** silence must be a choice, never a typo. Any input vocabulary
needs an explicit known-set check, and rejecting must write nothing.

## 5. A derived signal blind to what it summarizes

**H3 (0.3.0).** The ontology fingerprint emitted facts for 1 of 8 enforced
constraints, and none for edge-type properties. Two peers whose schemas
genuinely differed read `identical` — equal fingerprints, equal heads, both
integrity checks green, and disagreement about whether a node existed. The
code even had a `(true, true) => Identical // shouldn't happen` arm, which was
exactly the arm it fired through.

**The class:** a summary generated from a hand-written parallel list drifts
from the thing it summarizes. Generate facts by walking the structure the
validator itself consults, and pin it with a test parameterized over the
enforced set, so adding a rule without fingerprinting it fails the build.

**Corollary (S10):** fixing an emitter changes every fingerprint. Without a
version fact in the fingerprint, the fix reads as a fork against every peer on
the old formula.

## 6. A local decision applied remotely

**H1 (0.3.0).** A `Checkpoint` replaces the entire local oplog. Arriving over
sync, that silently deleted local writes the sender had never seen —
integrity green, nothing quarantined, nothing raised.
`verify_compaction_safe` cannot help: it guards the *compacting* peer, which
structurally cannot observe writes the receiver has not sent yet.

**The class:** an operation whose safety argument depends on local knowledge
must be refused when it arrives from outside. Foreign checkpoints are now
admissible only into a genesis-only oplog.

## 7. A "dead" field that is load-bearing

**S4 (0.3.0).** A field declared, never populated, never read — dead by every
reader-census measure. Deleting it changed the arity of every entry ever
written, because the encoding is positional. See the wire-format law in
`SKILL.md`.

## 8. Tests written to the observation, not the claim

**S6 (0.3.0).** The test for "two peers with identical oplogs produce
identical quarantine sets" asserted `len(a) > 0 or len(b) > 0` — a disjunction
that passes when the sets are maximally different, which is precisely the
failure the invariant forbids. Writing the honest assertion immediately failed
and surfaced a real defect.

**H7 (0.3.0).** Two CHANGELOG rows cited verification that did not exist: one
pointed at "an integration test in `src/python/mod.rs`", a file with zero
tests. `scripts/audit_claims.py` now fails on citations that do not resolve.

**The class:** a test whose assertion is weaker than the claim is a finding on
its own, whether or not it passes. Same for a citation nobody can follow.
