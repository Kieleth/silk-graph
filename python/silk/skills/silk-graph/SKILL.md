---
name: silk-graph
description: Use when working with the silk-graph library (`import silk`, `GraphStore`, `pip install silk-graph`) — defining or evolving an ontology, `extend_ontology`, writing typed nodes and edges, syncing between peers, diagnosing quarantined entries or an unexpectedly empty graph, compaction, persistence and reopen, or any silk error or ValueError from a silk call.
---

# Working with silk

Silk is an embedded Merkle-CRDT graph store: a typed property graph replicated
between peers with no server, no leader, and no coordinator. Rust core, Python
bindings, redb on disk.

## The model — internalize this first

**Every write is an entry in an append-only Merkle-DAG (the oplog). The graph
you query is derived from it by replay.** Three consequences that explain
almost every surprise:

1. **The oplog is authoritative; the ontology is entry #0.** The schema is not
   a side table — it is the genesis entry, content-addressed and replicated by
   the same machinery as the data. A declared ontology only *seeds* a new
   store. Reopening an existing store replays what the log says, not what your
   code passed to the constructor.
2. **Deletes are tombstones, not removals.** `remove_node` appends an entry.
   Cleaning up data makes the store *bigger* until you compact.
3. **Two failure modes, deliberately different.** A local write that violates
   the ontology raises immediately. An entry arriving over sync is
   *quarantined*: kept in the oplog for convergence, hidden from the graph.
   Silk never drops a peer's entry, because dropping it would break
   convergence.

## The traps

These cost real outages. Check them before debugging anything else.

**Store reopened and the graph looks empty or half-empty.** Almost always a
schema mismatch: entities whose type the current ontology does not know are
quarantined, not lost. `store.get_quarantined_details()` tells you exactly
why. Fix the schema and call `store.revalidate()`. The data is in the oplog
the whole time. Requires silk >= 0.3.0; older versions had two bugs here (see
`references/quarantine.md`).

**`extend_ontology` succeeded but nothing changed.** On silk < 0.4.0 an
extension whose keys silk did not recognize was accepted silently: it returned
a hash, appended an entry, and changed nothing. On >= 0.4.0 it raises. If you
are on an older version and a migration keeps "succeeding" while the schema
never moves, this is why. Upgrade.

**You cannot narrow anything, ever.** No removing a type, no removing a
property, no removing an endpoint binding, no adding a constraint to an
existing property. This is structural, not a policy: `OntologyExtension` has
no field that could express it. Plan schemas additively.

**Disk does not shrink when you delete.** `remove_*` appends. Even `compact()`
leaves the redb file at its high-water mark; pass `compact(reclaim_disk=True)`
to actually shrink it. Monitor `store.len()` (entry count), not file size.

**One writer per store file.** redb is single-writer. A second `GraphStore`
handle on the same path fails with "Database already open. Cannot acquire
lock." Close the first (`del store`) before opening another.

**Peers must run compatible protocol versions.** Silk's encoding is positional
— upgrading is all-or-nothing across a fleet, not one box at a time. An old
peer refuses a newer peer's sync offer cleanly rather than mis-parsing.

## Core usage

```python
from silk import GraphStore

ontology = {
    "node_types": {
        "server": {"properties": {"ip": {"value_type": "string"}}},
        "app":    {"properties": {}},
    },
    "edge_types": {
        "RUNS_ON": {"source_types": ["app"], "target_types": ["server"],
                    "properties": {}},
    },
}

store = GraphStore("inst-a", ontology, path="graph.redb")   # omit path = in-memory
store.add_node("s1", "server", "web-01", {"ip": "10.0.0.1"})
store.add_node("a1", "app", "api")
store.add_edge("e1", "RUNS_ON", "a1", "s1")

store.get_node("s1")                      # dict or None
store.query_nodes_by_type("server")       # includes subtypes and descendants
store.neighbors("a1"); store.shortest_path("a1", "s1")
```

Reopen an existing store with `GraphStore.open(path)` — no ontology argument,
it comes from the log. Passing an ontology to the constructor over an existing
store is for seeding, and a mismatch quarantines data rather than migrating it.

Sync is three calls, symmetric, and safe to repeat:

```python
offer   = local.generate_sync_offer()
payload = remote.receive_sync_offer(offer)
local.merge_sync_payload(payload)
```

## Rules of thumb

- Design the ontology additively; treat every type name as permanent.
- After any schema change, check `len(store.get_quarantined())`. Zero is the
  only good number in a healthy system.
- Never infer health from a green `verify_integrity()` — it checks DAG
  integrity, not whether your data is visible.
- `COMMITTED` means the write's shape was valid. It does not mean the value is
  true, and it does not mean every peer can see it.
- Wrap `pip install silk-graph` upgrades across a fleet as a single operation.

## Going deeper

Read the reference that matches the task; do not read them all.

| File | When |
|---|---|
| `references/ontology.md` | Defining or evolving a schema, `extend_ontology`, constraints, subtypes, hierarchy, compatibility checks |
| `references/quarantine.md` | Data is missing, a peer disagrees about what exists, diagnosing rejections, `revalidate()` |
| `references/persistence.md` | redb, compaction, disk reclamation, reopen semantics, flush modes, upgrades and protocol versions |
| `references/sync.md` | Peer sync, convergence, bootstrapping a new peer, checkpoints over the wire, gossip peer selection |

If you are changing silk itself rather than using it, the `silk-internals`
skill is the one you want.
