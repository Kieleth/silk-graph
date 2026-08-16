# Sync between peers

## The exchange

Three calls, and it is safe to repeat them at any cadence:

```python
offer   = local.generate_sync_offer()        # heads + bloom filter of what I have
payload = remote.receive_sync_offer(offer)   # what you're missing + what I need
local.merge_sync_payload(payload)            # returns count merged
```

That is one direction. Run it both ways for full convergence. Repeating a sync
that changes nothing is cheap and idempotent.

Convergence guarantee: any two peers that exchange sync messages converge to
the same oplog, without a leader or a coordinator. Concurrent writes to
different properties of the same node both survive (per-property
last-writer-wins). Concurrent add and remove resolves add-wins.

## Bootstrapping a new peer

Delta sync assumes a shared history. For a genuinely new peer, ship a snapshot:

```python
blob = source.snapshot()
new_peer = GraphStore.from_snapshot("inst-new", blob)
```

Then switch to ordinary sync. Bootstrapping by delta from a *compacted* source
also works only into an empty store — see below.

## Checkpoints over the wire

A checkpoint entry replaces the receiving oplog wholesale; that is what makes
compaction work. Over the wire it would be a data-loss weapon, so silk refuses
it: **a checkpoint authored by another peer is only accepted into a store
whose oplog is still genesis-only** (i.e. bootstrap). Otherwise it is rejected
with a message naming how many local entries it would have destroyed.

Practical rule: **compaction is a local storage decision.** Do not expect a
peer's compaction to propagate as compaction. If you want a fresh peer to
start from a compacted state, use `snapshot()` / `from_snapshot()`.

This protection requires silk >= 0.3.0. Before that, a routine compaction on
one peer silently deleted unsynced writes on every peer it synced to, with
integrity checks still green.

## Peer registry and gossip

```python
store.register_peer("peer-b", "tcp://b:7701")
store.record_sync("peer-b")          # after a successful exchange
store.list_peers(); store.unregister_peer("peer-b")
store.select_sync_targets()          # ceil(ln(N)+1) peers to contact this tick
```

For a fleet, gossip beats all-to-all: 10 peers → 4 targets per tick, 1000 → 8,
10000 → 10, converging in O(log N) rounds. The registry also feeds
`verify_compaction_safe()`, which is why `record_sync` matters.

## Cost, and why it matters at scale

An offer contains a bloom filter over **every** entry, and building it is
O(total entries) — not O(delta). At a 5-second sync cadence against a bloated
oplog, every round pays that cost even when both peers are already in sync.
Boot pays it too, since materializing replays the full log.

The practical consequence: **let the oplog grow unboundedly and sync cost
grows with it.** Compact on a policy (see `persistence.md`). A store that is
orders of magnitude over its expected size will make sync and boot slow enough
to look like a hang.

## Subscriptions

```python
sub_id = store.subscribe(lambda event: ...)   # fires on local writes and merges
store.unsubscribe(sub_id)
```

Events fire for entries merged from sync as well as local writes, and for
entries that *leave* quarantine when the ontology catches up — so a consumer
watching the subscription learns when previously-invisible data becomes
visible.

For durable cursors (resume where you left off rather than "from now"), see
`subscribe_from` and the tail-subscription APIs, plus
`register_subscriber_cursor` to stop compaction from cutting the ground out
from under a lagging consumer.

## Signing (optional)

```python
store.generate_signing_key(); store.get_public_key()
store.register_trusted_author("peer-b", pubkey)
store.set_require_signatures(True)     # reject unsigned entries on merge
```

Silk's default trust model is a **trusted peer network** — your own machines.
Signing exists for when that assumption is weaker, not as a substitute for it.

## Debugging a sync that "isn't working"

1. `local.len()` vs `remote.len()` — are the logs actually different?
2. `local.heads()` vs `remote.heads()` — equal heads means converged.
3. `len(local.get_quarantined())` — data may have arrived and be hidden.
   `get_quarantined_details()` says why. This is the most common answer.
4. `local.check_ontology_compatibility(...)` — advisory, but a `divergent`
   verdict explains a lot.
5. Protocol version mismatch — an older peer refuses a newer offer outright.
   Check the installed silk version on both ends.
