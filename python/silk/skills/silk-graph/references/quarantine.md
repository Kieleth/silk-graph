# Quarantine: why data is invisible, and how to get it back

## The two failure modes

**Local write, invalid → raises.** No entry is created. The caller finds out
immediately.

```python
store.add_node("n1", "unknown_type", "x")   # ValueError
```

**Sync arrival, invalid → quarantined.** The entry enters the oplog (so
convergence is preserved) but is skipped during materialization, so it is
invisible to every query. Nothing raises; nothing is logged by default.

This asymmetry is deliberate. Dropping a peer's entry would break convergence
— the two peers would have different logs forever. Quarantine keeps the data
and hides the interpretation.

## Diagnosing

```python
store.get_quarantined()          # ["<hex hash>", ...], sorted
store.get_quarantined_details()  # the actual reason (silk >= 0.3.0)
```

Each detail record:

```python
{"hash": "…", "op": "add_node",
 "reason": "unknown node type 'ufo'",
 "ontology_hash": "…"}     # the schema the decision was made against
```

`ontology_hash` matters: it tells you whether the diagnosis is stale relative
to the schema you are looking at now.

Every quarantined hash resolves — `store.get(h)` returns the entry, including
after compaction. If it does not, you are on a silk older than 0.3.0.

## Recovery

The whole point of quarantine is that it is reversible. Teach the store the
schema and the data appears:

```python
store.extend_ontology({"node_types": {"ufo": {"properties": {}}}})
# the extension triggers re-evaluation automatically
assert store.get_node("x1") is not None
```

If the ontology changed by some other route, force it:

```python
freed = store.revalidate()   # returns how many entries left quarantine
```

`revalidate()` re-materializes from the oplog and re-judges every entry
against the **current** ontology. It is idempotent and safe to call any time.

Re-evaluation happens automatically on both paths that change the schema: a
local `extend_ontology`, and a schema change arriving in a sync batch. The
public method exists so recovery never depends on guessing which trigger
fires.

## The invariants worth asserting in your own tests

- A hash is never both reported quarantined and materialized.
- Every hash in `get_quarantined()` resolves via `get()`.
- Two converged peers with the same base ontology report the same quarantine
  set. (Peers with *different* genesis ontologies legitimately differ — the
  quarantine decision is a function of base ontology plus oplog.)

## Common causes, most to least likely

1. **The peer knows a type you don't.** Their extension has not reached you,
   or you constructed with a narrower declared ontology. Fix: learn the type.
2. **A property violates a constraint you declare and they don't.** Their
   `cpu: 50` against your `max: 8`. Both peers are behaving correctly; the
   schemas disagree. Fix: reconcile the schemas, or widen yours.
3. **A declared type mismatch.** They wrote a string where you declare `int`.
4. **An edge whose endpoints are missing or wrongly typed.** Edges arriving
   before their endpoints are held pending and re-evaluated when the endpoints
   land; if they never land, the edge stays quarantined.
5. **A conflicting ontology extension.** Two peers added the same type name
   with different definitions. Exactly one wins, deterministically, on both
   peers; the loser's extension is quarantined.

## What quarantine is not

Not an error queue you should drain and delete. Not a sign of corruption. Not
something to bypass — there is no flag to force an invalid entry into the
graph, and adding one would end the convergence guarantee.

An entry that stays quarantined forever is a schema disagreement that nobody
has resolved. That is information, not a defect.
