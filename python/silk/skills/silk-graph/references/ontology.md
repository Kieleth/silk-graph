# Ontology: defining and evolving a schema

## Shape

```python
{
  "node_types": {
    "<type>": {
      "properties": {"<name>": {"value_type": "string|int|float|bool|list|map|any",
                                "required": false,
                                "constraints": {...}}},
      "parent_type": "<other type>",        # optional, RDFS-style is-a
      "subtypes": {"<name>": {"properties": {...}}},   # optional
    }
  },
  "edge_types": {
    "<TYPE>": {"source_types": ["..."], "target_types": ["..."],
               "properties": {...}}
  }
}
```

Pass it as a dict or a JSON string; both work everywhere an ontology is taken.

## Evolution: what `extend_ontology` accepts

Additive only. Any other shape is rejected.

| Key | Effect |
|---|---|
| `node_types` | Add a node type that does not exist yet |
| `edge_types` | Add an edge type that does not exist yet |
| `node_type_updates.<type>.add_properties` | New properties on an existing node type |
| `node_type_updates.<type>.relax_properties` | Flip required → optional |
| `node_type_updates.<type>.add_subtypes` | New subtypes on an existing node type |
| `edge_type_updates.<TYPE>.add_source_types` | Widen an existing edge's accepted sources |
| `edge_type_updates.<TYPE>.add_target_types` | Widen an existing edge's accepted targets |
| `edge_type_updates.<TYPE>.add_properties` | New properties on an existing edge type |

```python
store.extend_ontology({"node_types": {"database": {"properties": {}}}})
store.extend_ontology({"edge_type_updates": {
    "RUNS_ON": {"add_source_types": ["server"]}}})
```

It works on a **live persistent store**: the change appends to the oplog,
takes effect immediately, and survives reopen. There is no migration step and
no store recreation.

`edge_type_updates` requires silk >= 0.4.0. Before that, nothing could modify
an existing edge type at all — and the attempt was accepted silently, which
made migrations appear to succeed while changing nothing.

## What is not expressible, by design

Removing a type. Removing a property. Removing an endpoint binding. Changing a
property's `value_type`. Adding or tightening a constraint on an existing
property. Making an optional property required.

These are not "rejected by a rule" — `OntologyExtension` has no field that
could say them. The illegal state is unrepresentable, which is why concurrent
schema evolution converges without coordination.

The practical consequence: **a type name and a property's type are permanent
decisions.** If you need a different shape, add a new type alongside the old
one and migrate data by writing new entities.

## Errors that are errors, not no-ops (silk >= 0.4.0)

```python
store.extend_ontology({"edge_types_updates": {...}})  # typo -> ValueError, names the key
store.extend_ontology({})                             # no change -> ValueError
store.extend_ontology({"edge_type_updates": {"R": {"add_source_types": ["app"]}}})
                                                      # already bound -> ValueError
```

Nothing is written to the oplog when the call raises. On older versions all
three returned a hash and appended a no-op entry that replicated to peers.

## Constraints

Enforced names, and only these:

`enum`, `min`, `max`, `min_exclusive`, `max_exclusive`, `min_length`,
`max_length`, `pattern`

```python
from silk import enforced_constraint_names
enforced_constraint_names()   # authoritative for the installed build
```

An unknown constraint name is **rejected at construction** (silk >= 0.3.0),
because a typo like `maximum` for `max` would otherwise be inert forever and
invisible to compatibility checks. To declare a constraint you know silk does
not enforce, prefix it `x_`:

```python
{"value_type": "int", "constraints": {"x_business_rule": "..."}}   # accepted, never checked
```

## Open properties (D-026)

Properties **not declared** in the ontology are accepted without validation.
The ontology defines the minimum, not the maximum. This is deliberate and
documented; it is not a hole to fix. If you need a property validated, declare
it — but note that declaring it does not retroactively validate values already
written.

There is no strict mode. If your application needs one, enforce it at your
layer.

## Hierarchy

`parent_type` gives RDFS-level is-a: property inheritance, hierarchy-aware
queries (`query_nodes_by_type("entity")` returns descendants too), and
hierarchy-aware edge validation (`source_types: ["entity"]` accepts any
descendant). No OWL reasoning, no inference — validation only.

Subtypes are a separate mechanism from `parent_type`: a subtype is a variant
of one type with extra properties, selected per node via `subtype=`.

## Compatibility between peers

```python
verdict = local.check_ontology_compatibility(remote.ontology_hash(),
                                             remote.ontology_fingerprint())
# "identical" | "superset" | "subset" | "divergent"
```

**Advisory only — silk never calls this during sync.** Real compatibility is
behavioral: entries merge blind, anything the local ontology rejects is
quarantined, and an arriving schema change triggers re-evaluation. Call it
yourself if you want to warn before syncing, or to classify a migration.

Reading the verdicts:

- `superset` — you know everything the peer knows, plus more. Safe. A widened
  edge binding produces exactly this, which makes it a useful "safe to
  proceed" signal for a migration guard.
- `subset` — the peer knows more. Their extra types will quarantine locally
  until you learn them.
- `divergent` — genuine fork, not resolvable by additive evolution.

The fingerprint is a set of atomic facts (types, properties, constraints,
edge endpoints, and an emitter version). Requires silk >= 0.3.0 to be
trustworthy: earlier versions emitted facts for only one of eight constraints,
so peers with genuinely different schemas could read `identical`.
