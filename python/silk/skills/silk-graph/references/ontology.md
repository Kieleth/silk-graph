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

Additive only, where additive means **no entry already in the oplog stops
validating**. That is stricter than "the new declarations are a superset of
the old ones", and the difference is where the data loss lived until 0.5.0.

| Key | Effect |
|---|---|
| `node_types` | Add a node type that does not exist yet. Required properties are fine here: no data can exist under a type that does not exist |
| `edge_types` | Add an edge type that does not exist yet |
| `node_type_updates.<type>.add_properties` | New **optional** properties on an existing node type |
| `node_type_updates.<type>.relax_properties` | Flip required → optional |
| `node_type_updates.<type>.add_subtypes` | New subtypes, on a type that **already declares at least one**, carrying no required properties |
| `edge_type_updates.<TYPE>.add_source_types` | Widen an existing edge's accepted sources |
| `edge_type_updates.<TYPE>.add_target_types` | Widen an existing edge's accepted targets |
| `edge_type_updates.<TYPE>.add_properties` | New **optional** properties on an existing edge type |

```python
store.extend_ontology({"node_types": {"database": {"properties": {}}}})
store.extend_ontology({"edge_type_updates": {
    "RUNS_ON": {"add_source_types": ["server"]}}})
```

It works on a **live persistent store**: the change appends to the oplog,
takes effect immediately, and survives reopen. There is no migration step and
no store recreation.

`edge_type_updates` requires silk >= 0.4.0. Before that, nothing could modify
an existing edge type at all, and the attempt was accepted silently, which made
migrations appear to succeed while changing nothing.

## Extensions that would delete your data (silk >= 0.5.0)

Four shapes are additive on the declarations and destructive in effect. They
are refused, and the message says what would have happened:

```python
# Every node written before this has no subtype, and a type that declares
# subtypes requires one on every node.
store.extend_ontology({"node_type_updates": {"host": {"add_subtypes": {...}}}})
# -> ValueError: cannot add subtypes ["vm"] to node type 'host', which
#    declares none: ... every 'host' node written so far would be quarantined

# Every entry written before this lacks the property.
store.extend_ontology({"node_type_updates": {"host": {"add_properties": {
    "owner": {"value_type": "string", "required": True}}}}})
# -> ValueError: cannot add required property 'owner' to node type 'host': ...
#    Add it as optional (required: false) instead
```

The same refusal covers a required property on an existing **edge** type, and
one inside a **newly declared subtype** (a node may already be naming that
subtype: silk accepts an undeclared subtype name, validating type-level
properties only).

Practical consequences, both permanent decisions made at genesis:

- **A type that may ever have subtypes must declare one when it is created.**
  `"subtypes": {}` does not reserve the slot; it refuses every node with
  `allowed: []`. Declare a real member, even a `"generic"` one.
- **Required properties are genesis-only for a given type.** Afterwards, add
  optional and enforce it in your own write path, or declare a new type.

Before 0.5.0 all four were accepted, and every affected entry quarantined
inside the `extend_ontology` call. If you are on 0.4.x or older, check with
`preview_extension` first.

## The fifth shape, which cannot be refused: `preview_extension`

A node written with a property the ontology does not declare is accepted.
Declaring that property later, **even as optional**, type-checks it
retroactively:

```python
store.add_node("n1", "host", "N", {"name": "a", "port": "8080"})   # port undeclared, fine
store.extend_ontology({"node_type_updates": {"host": {"add_properties": {
    "port": {"value_type": "int", "required": False}}}}})           # accepted
store.get_node("n1")                                                # None
```

Nothing about that extension is dangerous. The danger is in the values already
stored, so it stays legal. `preview_extension` replays the oplog against the
would-be ontology and tells you before you commit:

```python
result = store.preview_extension(extension)
# {"would_quarantine": 1,
#  "would_rescue": 0,
#  "samples": [{"hash": "...", "op": "add_node", "reason": "'host'.'port' expects Int, got string"}]}
if result["would_quarantine"]:
    ...   # do not extend
store.extend_ontology(extension)
```

`would_rescue` counts the other direction: entries quarantined now that the
extension would make valid. It raises whatever `extend_ontology` would raise,
so a refused extension is refused here too rather than reported as free.

Run it before every extension on a store with data in it. It is the only check
that sees this class.

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
# "identical" | "superset" | "destructive_superset" | "subset" | "divergent"
```

**Advisory only — silk never calls this during sync.** Real compatibility is
behavioral: entries merge blind, anything the local ontology rejects is
quarantined, and an arriving schema change triggers re-evaluation. Call it
yourself if you want to warn before syncing, or to classify a migration.

Reading the verdicts:

- `superset` — you know everything the peer knows, plus more, and nothing they
  hold stops validating. Safe. A widened edge binding produces exactly this,
  which makes it a useful "safe to proceed" signal for a migration guard.
- `destructive_superset` (silk >= 0.5.0) — you declare more than the peer, but
  the extra declarations include a requirement their data was never held to.
  Adopting your ontology would quarantine entries they consider valid. Refuse.
  `ontology_destructive_changes(peer_fingerprint)` lists which changes and why.
- `subset` — the peer knows more. Their extra types will quarantine locally
  until you learn them.
- `divergent` — genuine fork, not resolvable by additive evolution.

Before 0.5.0 the destructive cases returned plain `superset`, indistinguishable
from a safe one. A boot guard that auto-migrated on `superset` migrated into
data loss. The new value is deliberately a **new string**: existing code that
matches `== "superset"` falls through to its refuse branch with no change.

The verdict works from fingerprints alone, so it needs no access to the peer's
oplog. That is also its limit: it cannot see a change whose destructiveness
depends on stored values. `preview_extension` is the tool for that, and it
needs the actual store.

The fingerprint is a set of atomic facts (types, properties, constraints,
edge endpoints, and an emitter version). Requires silk >= 0.3.0 to be
trustworthy: earlier versions emitted facts for only one of eight constraints,
so peers with genuinely different schemas could read `identical`.
