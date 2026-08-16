# Persistence, compaction, and upgrades

## Opening a store

```python
GraphStore("inst-a", ontology, path="graph.redb")  # create OR seed-and-open
GraphStore.open("graph.redb")                      # reopen; schema comes from the log
GraphStore("inst-a", ontology)                     # in-memory, no path
```

`GraphStore.open()` is the right call for an existing store: it takes the
ontology from genesis. The constructor form is for creating a store, and when
pointed at an existing one the declared ontology only seeds — the log wins,
and any type the declared ontology omits will quarantine data rather than
migrate it.

**redb is single-writer.** One live handle per file. A second open raises
`Database already open. Cannot acquire lock.` Release the first with `del
store` first. This is an availability constraint, not an ontology one.

## Flush modes

```python
store.set_flush_mode("immediate")   # default: every write fsyncs. Safe, ~1000x slower.
store.set_flush_mode("deferred")    # buffer in memory
store.flush()                       # returns entries flushed
store.pending_flush_count()
```

Deferred gives read-your-writes immediately (the entry is in the oplog) but on
crash the unflushed entries are lost locally. Peers restore them on next sync
if they had already been shared.

## Compaction

The oplog grows forever without it. Tombstones and superseded values are never
removed by ordinary operation.

```python
store.compact()                       # fold the whole log into one checkpoint
store.compact(reclaim_disk=True)      # ...and shrink the file
store.compact(safe=False)             # skip the peer-sync safety check
store.verify_compaction_safe()        # (bool, [reasons])
```

Three things to know:

**Deleting data makes the store bigger.** `remove_node` appends an entry, and
so does every property update. A cleanup pass grows the log until compaction.
Entry count (`store.len()`) is the honest health metric.

**The file does not shrink by default.** `compact()` folds the log but leaves
the redb file at its high-water mark; freed pages get reused. Pass
`reclaim_disk=True` to actually shrink it. Measured on a debris-heavy store:
2580 KB → 2580 KB with plain compact, → 672 KB with reclamation. redb keeps a
floor of roughly 670 KB, so a near-empty store will not go below that — do not
set a size alarm under it.

**Compaction is a local decision, and it is destructive to history.**
Pre-checkpoint entries are gone; their hashes no longer resolve. Provenance
(`entries_affecting`) still works but folds pre-checkpoint writes into the
checkpoint. Quarantined entries are carried across rather than dropped (silk
>= 0.3.0), but they get new hashes.

Safety with peers:

```python
store.register_peer("peer-b", "tcp://b:7701")
store.record_sync("peer-b")           # call after a successful sync
safe, reasons = store.verify_compaction_safe()
```

The check only knows about peers you registered. It protects the *compacting*
peer's assumptions; it cannot protect a peer that holds writes it has not sent
you yet. That is why a foreign checkpoint is refused unless the receiving
store is empty (see `sync.md`).

Automate it with a policy rather than by hand:

```python
from silk import ThresholdPolicy, IntervalPolicy
policy = ThresholdPolicy(max_entries=1000, reclaim_disk=True)
policy.check(store)     # compacts if over threshold; returns hash or None
```

## Upgrades and protocol versions

Silk serializes entries as a **positional** msgpack array — no field names on
the wire or on disk. Field count and order are the format. Adding or removing
a field is a protocol change, which is why versions move faster than a library
this age would suggest.

| Version | PROTOCOL_VERSION | Change |
|---|---|---|
| ≤ 0.2.7 | 1 | — |
| 0.3.0 | 2 | `Entry` lost a never-populated field |
| 0.4.0 | 3 | `OntologyExtension` gained `edge_type_updates` |

Compatibility is **one-directional in every case**: a newer build reads
everything older builds wrote (shims accept the legacy arities), and an older
build cannot read newer entries. An old peer receiving a newer sync offer
refuses it cleanly rather than mis-parsing.

**Therefore: upgrade a fleet together, not one box at a time.** A partial
upgrade does not corrupt anything, but sync between the halves stops. Verify
before rolling: open a copy of a production store with the new build and
check `len()`, node counts, and `get_quarantined()`.

## Time travel and snapshots

```python
snap = store.as_of(physical_ms, logical)   # read-only view at a point in time
blob = store.snapshot()                    # bytes, for bootstrapping a peer
GraphStore.from_snapshot("inst-b", blob)
```

`as_of` cannot see behind a checkpoint: compaction removes the history it
would need.
