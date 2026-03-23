# Silk Sync Protocol Specification

This document specifies the wire format and protocol for peer-to-peer synchronization between Silk graph stores. It is intended for implementors building Silk-compatible peers in any language.

## Overview

Silk sync is a 3-step delta-state CRDT protocol:

```
Peer A                          Peer B
  │                               │
  ├── generate_sync_offer() ─────►│  "Here's what I have" (Bloom filter + heads)
  │                               │
  │◄── receive_sync_offer() ──────┤  "Here's what you're missing" (entries)
  │                               │
  ├── merge_sync_payload() ───────┤  Apply remote entries, re-materialize graph
  │                               │
```

For full convergence, both peers must complete this exchange (A→B, then B→A). After both rounds, both stores hold identical materialized graphs.

## Serialization

All messages are serialized as [MessagePack](https://msgpack.org/) using `rmp_serde` (Rust). Fields are serialized in struct declaration order. MessagePack is schema-free — the receiver must know the expected structure.

**Library**: `rmp-serde` 1.x (Rust), `msgpack` (Python), or any MessagePack implementation.

## Data Types

### Hash

A 32-byte BLAKE3 hash, represented as `[u8; 32]`. In MessagePack: a `bin 32` (binary data, 32 bytes). In hex display: 64 lowercase hex characters.

### HybridClock (R-01)

```
{
    "id": string,          // Instance identifier (e.g., "node-a")
    "physical_ms": uint64, // Wall-clock time in milliseconds since Unix epoch
    "logical": uint32      // Counter for events within same millisecond
}
```

MessagePack: a 3-element map with string keys.

**Rules:**
- On local event (`tick`): physical = max(old_physical, wall_clock). If physical advanced → logical = 0. Else → logical += 1.
- On merge: physical = max(local, remote, wall_clock). If physical advanced past both → logical = 0. If tied with one side → logical = max(tied) + 1.
- Total order: (physical_ms, logical, id). Higher physical wins. Same → higher logical wins. Both equal → lower id wins.

### Value (property values)

```
Null    → msgpack nil
Bool    → msgpack bool
Int     → msgpack int64
Float   → msgpack float64
String  → msgpack str
List    → msgpack array of Value
Map     → msgpack map of (str → Value)
```

Serialized with `#[serde(untagged)]` — no type tag. The MessagePack type byte determines the Value variant.

### GraphOp (entry payload)

Serialized with `#[serde(tag = "op")]` — an `"op"` field discriminates the variant.

**define_ontology** (genesis — must be the first entry):
```json
{"op": "define_ontology", "ontology": {...}}
```

**add_node**:
```json
{"op": "add_node", "node_id": "alice", "node_type": "person", "subtype": null, "label": "Alice", "properties": {"age": 30}}
```

**add_edge**:
```json
{"op": "add_edge", "edge_id": "e1", "edge_type": "WORKS_AT", "source_id": "alice", "target_id": "acme", "properties": {}}
```

**update_property**:
```json
{"op": "update_property", "entity_id": "alice", "key": "age", "value": 31}
```

**remove_node**:
```json
{"op": "remove_node", "node_id": "alice"}
```

**remove_edge**:
```json
{"op": "remove_edge", "edge_id": "e1"}
```

**extend_ontology** (R-03 — monotonic schema evolution):
```json
{"op": "extend_ontology", "extension": {
  "node_types": {"service": {"properties": {"url": {"value_type": "string"}}}},
  "edge_types": {},
  "node_type_updates": {
    "entity": {
      "add_properties": {"region": {"value_type": "string", "required": false}},
      "relax_properties": ["status"],
      "add_subtypes": {}
    }
  }
}}
```

Only additive changes allowed: add types, add properties, add subtypes, relax required->optional. Cannot remove types, remove properties, or tighten constraints.

### Entry

The atomic unit of the Merkle-DAG. Content-addressed: `hash = BLAKE3(msgpack(signable_content))`.

```
{
    "hash":    [u8; 32],          // BLAKE3 hash (see below)
    "payload": GraphOp,           // The graph mutation
    "next":    [[u8; 32], ...],   // Causal predecessors (DAG heads at write time)
    "refs":    [[u8; 32], ...],   // Skip-list references (O(log n) traversal)
    "clock":   HybridClock,       // Hybrid clock at creation (R-01)
    "author":  string,            // Instance ID that created this entry
    "signature": Option<Vec<u8>>  // D-027: ed25519 signature over hash (64 bytes), None for unsigned
}
```

**Hash computation:**

The hash covers everything except the hash itself. The "signable content" is:

```rust
struct SignableContent {
    payload: GraphOp,
    next: Vec<Hash>,
    refs: Vec<Hash>,
    clock: HybridClock,
    author: String,
}
```

Serialized to MessagePack bytes, then hashed: `hash = BLAKE3(msgpack(signable_content))`.

**To verify an entry**: recompute `BLAKE3(msgpack({payload, next, refs, clock, author}))` and compare to the `hash` field.

## Signature Verification (D-027)

Entries may carry an ed25519 signature over the `hash` field. The signature covers the BLAKE3 hash (which already covers payload, next, refs, clock, and author).

### Signing flow

1. Compute `hash = BLAKE3(msgpack(SignableContent))` (same as unsigned)
2. `signature = ed25519_sign(signing_key, hash)` (64 bytes)
3. Entry includes `signature: Some(sig_bytes)`

### Verification flow

On merge, for each incoming entry:
1. `verify_hash()` — recompute BLAKE3, reject if mismatch
2. If `signature` is present:
   - Look up `author` in the trust registry → get public key
   - `ed25519_verify(public_key, entry.hash, signature)` → reject if invalid
   - If author not in registry → reject (in strict mode) or accept with warning
3. If `signature` is absent:
   - In strict mode (`require_signatures=true`): reject (except genesis entries)
   - In default mode: accept (backward compatibility)

### Backward Compatibility

- Old entries (pre-D-027) have `signature: null` (via `#[serde(default)]`)
- New entries include the signature field
- Both coexist in the same oplog and sync correctly
- Strict mode is opt-in — default accepts unsigned entries

## Protocol Messages

### SyncOffer

Sent by the initiating peer. Advertises its state.

```
{
    "heads":      [[u8; 32], ...],   // Current DAG head hashes
    "bloom":      BloomFilter,        // Bloom filter of all entry hashes
    "physical_ms": uint64,            // Current wall-clock time (ms)
    "logical":     uint32             // Current logical counter
}
```

**Serialization**: `msgpack(SyncOffer)`, then transmitted as raw bytes.

### SyncPayload

Response to a SyncOffer. Contains entries the offerer is missing.

```
{
    "entries": [Entry, ...],    // Entries the peer needs, in topological order
    "need":   [[u8; 32], ...]   // Hashes the sender still needs (false-positive resolution)
}
```

**Topological order**: entries are sorted so that every entry's `next` references appear before it in the list. This allows the receiver to apply them in order without missing dependencies.

### Snapshot

Full state transfer for bootstrapping new peers.

```
{
    "entries": [Entry, ...]   // ALL entries, in topological order
}
```

## Bloom Filter

Used in SyncOffer to compactly represent which entries the peer has.

### Parameters

```
{
    "bits":       [uint64, ...],   // Bit array, packed into 64-bit words
    "num_bits":   usize,           // Total number of bits
    "num_hashes": uint32,          // Number of hash functions (k)
    "count":      usize            // Number of items inserted
}
```

**Sizing formulas** (standard Bloom filter):
- `m = ceil(-n * ln(p) / ln(2)^2)` where n = expected items, p = false positive rate (default 0.01)
- `k = ceil((m/n) * ln(2))`
- Minimum 64 bits

**Hash function**: BLAKE3 of the entry hash bytes. The 32-byte BLAKE3 output is sliced into `k` segments, each modulo `num_bits`, to produce `k` bit positions.

**False positive rate**: ~1% with default parameters. False positives mean an entry is incorrectly reported as "already have it." The `need` field in SyncPayload handles this — if a DAG head is a false positive, the receiver explicitly requests it.

## Protocol Flow

### Normal Sync (Two Peers)

```
A                                    B
│                                    │
│  1. SyncOffer (A's heads + bloom) ──►
│                                    │
│     B computes entries_missing:    │
│     - Walk A's heads              │
│     - For each entry NOT in       │
│       A's bloom → include         │
│     - Force B's heads if not      │
│       in A's heads (C-075)        │
│                                    │
│  ◄── 2. SyncPayload (entries + need)
│                                    │
│  A merges entries into OpLog      │
│  A re-materializes graph          │
│                                    │
│  (Repeat in reverse: B→A)         │
│                                    │
```

### Head Forcing (C-075)

If a Bloom filter false positive hits a DAG-tip entry (head), no ancestor closure can recover it. The fix: always include local heads in the sync payload when they aren't in the remote's heads set. Sending entries the remote already has is harmless (merge is idempotent). Not sending entries the remote needs is data loss.

### Bootstrap (New Peer)

```
A (existing)                    C (new)
│                                │
│  1. C requests snapshot ──────►│
│                                │
│  ◄── 2. Snapshot (all entries) │
│                                │
│  C creates OpLog from entries  │
│  C materializes graph          │
│  C switches to delta sync      │
│                                │
```

## Merge Semantics

### Entry Merge

- Entries are identified by hash — identical hashes are the same entry
- Merging is idempotent: applying the same entry twice has no effect
- Out-of-order entries are retried: if an entry references unknown parents, it's queued and retried after other entries are applied
- The OpLog accepts any valid entry regardless of arrival order

### Conflict Resolution

- **Add-wins**: If one peer removes a node and another adds an edge to it, the add wins after sync
- **Per-property LWW**: Concurrent updates to the same property are resolved by Hybrid Logical Clock comparison. Higher physical_ms wins. Same physical_ms: higher logical wins. Both equal: lexicographically lower instance ID wins.
- **Non-conflicting concurrent writes**: Two peers updating different properties on the same node both succeed — neither is lost

## Quarantine (R-02)

Invalid entries (failing ontology validation) are accepted into the oplog for CRDT convergence but quarantined from the materialized graph. Quarantine is local policy — the oplog is identical across all peers.

- Entry arrives -> hash verified -> appended to oplog (always)
- During materialization: validate payload against current ontology
- If invalid -> hash added to `quarantined` set, entry skipped for materialization
- Quarantine is grow-only (monotonic). Entries never leave quarantine.
- Queries (`get_node`, `all_nodes`, etc.) never return quarantined data

## Version Compatibility

The current protocol has no version field in messages. Any change to the Entry format, GraphOp variants, or Bloom filter structure is a breaking change requiring a major version bump of the library.

D-027 adds an optional `signature` field to Entry. This is backward compatible — old entries deserialize with `signature: null`.

Future versions may add a protocol version field to SyncOffer for negotiation.
