use serde::{Deserialize, Serialize};
use std::collections::{HashSet, VecDeque};

use crate::bloom::BloomFilter;
use crate::entry::{Entry, Hash};
use crate::oplog::OpLog;

/// S-03: Maximum byte size for sync messages (64 MB).
const MAX_SYNC_BYTES: usize = 64 * 1024 * 1024;
/// S-03: Maximum entries in a single sync payload or snapshot.
const MAX_ENTRIES_PER_MESSAGE: usize = 100_000;

/// A sync offer — sent by a peer to advertise its state.
///
/// Contains the peer's current DAG heads and a bloom filter of all
/// entry hashes it holds. The recipient uses this to compute which
/// entries the peer is missing and needs to receive.
/// Current protocol version. Incremented on breaking wire format changes.
pub const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncOffer {
    /// Protocol version — peers reject offers with unknown versions.
    /// Defaults to 0 for backward compat with pre-versioned offers.
    #[serde(default)]
    pub protocol_version: u32,
    /// Current DAG heads of the offering peer.
    pub heads: Vec<Hash>,
    /// Bloom filter containing all entry hashes the peer has.
    pub bloom: BloomFilter,
    /// Physical time (ms) of the offering peer's clock.
    pub physical_ms: u64,
    /// Logical counter of the offering peer's clock.
    pub logical: u32,
}

/// A sync response — entries the recipient should merge.
///
/// Contains the entries the peer is missing (not in their bloom filter
/// and not reachable from their heads).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncPayload {
    /// Entries the peer is missing, in topological (causal) order.
    pub entries: Vec<Entry>,
    /// Hashes the sender still needs (explicit request for false-positive resolution).
    pub need: Vec<Hash>,
}

/// A full snapshot — for bootstrapping new peers.
///
/// Contains every entry in the op log, serialized in topological order.
/// New peers deserialize this, rebuild their op log and materialized graph,
/// then switch to delta sync.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    /// All entries in topological (causal) order.
    pub entries: Vec<Entry>,
}

impl SyncOffer {
    /// Build a sync offer from an op log.
    ///
    /// Constructs a bloom filter of all entry hashes and captures current heads.
    pub fn from_oplog(oplog: &OpLog, physical_ms: u64, logical: u32) -> Self {
        let all = oplog.entries_since(None);
        // Use a minimum of 128 expected items so the bloom filter has enough
        // bits to avoid false positives with very small entry sets.
        let count = all.len().max(128);
        let mut bloom = BloomFilter::new(count, 0.01);
        for entry in &all {
            bloom.insert(&entry.hash);
        }
        Self {
            protocol_version: PROTOCOL_VERSION,
            heads: oplog.heads(),
            bloom,
            physical_ms,
            logical,
        }
    }

    /// Serialize to MessagePack bytes.
    pub fn to_bytes(&self) -> Vec<u8> {
        rmp_serde::to_vec(self).expect("sync offer serialization should not fail")
    }

    /// Deserialize from MessagePack bytes.
    /// S-03: validates byte length. S-05: validates bloom filter dimensions.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > MAX_SYNC_BYTES {
            return Err(format!(
                "sync offer too large: {} bytes (max {MAX_SYNC_BYTES})",
                bytes.len()
            ));
        }
        let offer: Self =
            rmp_serde::from_slice(bytes).map_err(|e| format!("invalid sync offer: {e}"))?;
        // Protocol version check — reject offers from incompatible future versions
        if offer.protocol_version > PROTOCOL_VERSION {
            return Err(format!(
                "unsupported protocol version {} (this peer supports up to {})",
                offer.protocol_version, PROTOCOL_VERSION
            ));
        }
        offer
            .bloom
            .validate()
            .map_err(|e| format!("invalid bloom filter in sync offer: {e}"))?;
        Ok(offer)
    }
}

impl SyncPayload {
    /// Serialize to MessagePack bytes.
    pub fn to_bytes(&self) -> Vec<u8> {
        rmp_serde::to_vec(self).expect("sync payload serialization should not fail")
    }

    /// Deserialize from MessagePack bytes.
    /// S-03: validates byte length and entry count.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > MAX_SYNC_BYTES {
            return Err(format!(
                "sync payload too large: {} bytes (max {MAX_SYNC_BYTES})",
                bytes.len()
            ));
        }
        let payload: Self =
            rmp_serde::from_slice(bytes).map_err(|e| format!("invalid sync payload: {e}"))?;
        if payload.entries.len() > MAX_ENTRIES_PER_MESSAGE {
            return Err(format!(
                "too many entries in payload: {} (max {MAX_ENTRIES_PER_MESSAGE})",
                payload.entries.len()
            ));
        }
        Ok(payload)
    }
}

impl Snapshot {
    /// Build a full snapshot from an op log.
    pub fn from_oplog(oplog: &OpLog) -> Self {
        let entries: Vec<Entry> = oplog.entries_since(None).into_iter().cloned().collect();
        Self { entries }
    }

    /// Serialize to MessagePack bytes.
    pub fn to_bytes(&self) -> Vec<u8> {
        rmp_serde::to_vec(self).expect("snapshot serialization should not fail")
    }

    /// Deserialize from MessagePack bytes.
    /// S-03: validates byte length and entry count.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > MAX_SYNC_BYTES {
            return Err(format!(
                "snapshot too large: {} bytes (max {MAX_SYNC_BYTES})",
                bytes.len()
            ));
        }
        let snap: Self =
            rmp_serde::from_slice(bytes).map_err(|e| format!("invalid snapshot: {e}"))?;
        if snap.entries.len() > MAX_ENTRIES_PER_MESSAGE {
            return Err(format!(
                "too many entries in snapshot: {} (max {MAX_ENTRIES_PER_MESSAGE})",
                snap.entries.len()
            ));
        }
        Ok(snap)
    }
}

/// Compute the entries that a remote peer is missing.
///
/// Given a remote peer's sync offer (heads + bloom filter), determine which
/// entries from our local op log the peer doesn't have and should receive.
///
/// Uses bloom filter for fast "probably has it" checks. Entries definitely
/// in the bloom filter are skipped; entries not in the bloom are included.
///
/// To prevent false positives from breaking causal chains, the payload
/// includes the transitive closure of ancestors for every missing entry.
/// If a parent was false-positively skipped by the bloom filter, it gets
/// included anyway because a descendant needs it.
pub fn entries_missing(oplog: &OpLog, remote_offer: &SyncOffer) -> SyncPayload {
    let remote_heads_set: HashSet<Hash> = remote_offer.heads.iter().copied().collect();

    // Get all our entries.
    let all_entries = oplog.entries_since(None);

    // Check if remote already has all our heads.
    let our_heads: HashSet<Hash> = oplog.heads().into_iter().collect();
    if our_heads.is_subset(&remote_heads_set) {
        // Remote is up-to-date (or ahead). Nothing to send.
        // But we might need entries from them.
        let need = compute_need(oplog, &remote_offer.heads);
        return SyncPayload {
            entries: vec![],
            need,
        };
    }

    // Phase 1: collect entries the bloom says the remote doesn't have.
    let mut send_set: HashSet<Hash> = HashSet::new();
    for entry in &all_entries {
        if !remote_offer.bloom.contains(&entry.hash) {
            send_set.insert(entry.hash);
        }
    }

    // Phase 1.5: Force our heads into send_set if the remote doesn't have
    // them as heads. Bloom filter false positives on head entries (DAG tips)
    // cannot be recovered by Phase 2's ancestor closure because no descendant
    // exists in send_set to trigger the walk. Forcing heads guarantees they
    // are always sent, and Phase 2 then pulls in their full causal chain.
    for &head in &our_heads {
        if !remote_heads_set.contains(&head) {
            send_set.insert(head);
        }
    }

    // Phase 2: ancestor closure — for each entry we're sending, ensure
    // all parents are either in the remote's heads OR in our send set.
    // This recovers bloom filter false positives that would break causal chains.
    //
    // EXP-01 fix: BFS queue instead of O(n × depth) nested loop.
    // The old code iterated ALL entries per pass, needing O(depth) passes.
    // For a 900-entry linear chain: 900 × 1000 = 900K iterations.
    // BFS processes each entry at most once: O(|send_set| + |ancestors|).
    {
        let mut queue: VecDeque<Hash> = send_set.iter().copied().collect();
        while let Some(hash) = queue.pop_front() {
            if let Some(entry) = oplog.get(&hash) {
                for parent_hash in &entry.next {
                    if !send_set.contains(parent_hash)
                        && !remote_heads_set.contains(parent_hash)
                        && oplog.get(parent_hash).is_some()
                    {
                        send_set.insert(*parent_hash);
                        queue.push_back(*parent_hash);
                    }
                }
            }
        }
    }

    // Build the payload in topological order.
    let missing: Vec<Entry> = all_entries
        .into_iter()
        .filter(|e| send_set.contains(&e.hash))
        .cloned()
        .collect();

    // Compute what we need from the remote.
    let need = compute_need(oplog, &remote_offer.heads);

    SyncPayload {
        entries: missing,
        need,
    }
}

/// Compute which remote heads we don't have (we need them).
fn compute_need(oplog: &OpLog, remote_heads: &[Hash]) -> Vec<Hash> {
    remote_heads
        .iter()
        .filter(|h| oplog.get(h).is_none())
        .copied()
        .collect()
}

/// Merge remote entries into a local op log.
///
/// Entries are validated (hash verification, parent existence) and appended.
/// Returns the number of new entries successfully merged.
///
/// Entries should be in topological order (parents before children).
/// If an entry's parents haven't arrived yet, it's retried after processing
/// the rest of the batch (handles minor ordering issues).
pub fn merge_entries(oplog: &mut OpLog, entries: &[Entry]) -> Result<usize, String> {
    let mut inserted = 0;
    let mut remaining: Vec<&Entry> = entries.iter().collect();
    let mut max_passes = remaining.len() + 1;

    while !remaining.is_empty() && max_passes > 0 {
        let mut next_remaining = Vec::new();
        for entry in &remaining {
            match oplog.append((*entry).clone()) {
                Ok(true) => {
                    inserted += 1;
                }
                Ok(false) => {
                    // Duplicate — already have it, skip.
                }
                Err(crate::oplog::OpLogError::MissingParent(_)) => {
                    // Parent not yet available — retry later in the batch.
                    next_remaining.push(*entry);
                }
                Err(crate::oplog::OpLogError::InvalidHash) => {
                    return Err(format!(
                        "invalid hash for entry {}",
                        hex::encode(entry.hash)
                    ));
                }
            }
        }
        if next_remaining.len() == remaining.len() {
            // No progress — remaining entries have unresolvable parents.
            return Err(format!(
                "{} entries have unresolvable parents",
                remaining.len()
            ));
        }
        remaining = next_remaining;
        max_passes -= 1;
    }

    Ok(inserted)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::LamportClock;
    use crate::entry::GraphOp;
    use crate::ontology::{NodeTypeDef, Ontology};
    use std::collections::BTreeMap;

    fn test_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([(
                "entity".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            )]),
            edge_types: BTreeMap::new(),
        }
    }

    fn genesis(author: &str) -> Entry {
        Entry::new(
            GraphOp::DefineOntology {
                ontology: test_ontology(),
            },
            vec![],
            vec![],
            LamportClock::new(author),
            author,
        )
    }

    fn add_node_op(id: &str) -> GraphOp {
        GraphOp::AddNode {
            node_id: id.into(),
            node_type: "entity".into(),
            label: id.into(),
            properties: BTreeMap::new(),
            subtype: None,
        }
    }

    fn make_entry(op: GraphOp, next: Vec<Hash>, clock_time: u64, author: &str) -> Entry {
        Entry::new(
            op,
            next,
            vec![],
            LamportClock::with_values(author, clock_time, 0),
            author,
        )
    }

    // -- SyncOffer tests --

    #[test]
    fn sync_offer_from_oplog() {
        let g = genesis("inst-a");
        let mut log = OpLog::new(g.clone());
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        log.append(e1.clone()).unwrap();

        let offer = SyncOffer::from_oplog(&log, 2, 0);
        assert_eq!(offer.heads, vec![e1.hash]);
        assert!(offer.bloom.contains(&g.hash));
        assert!(offer.bloom.contains(&e1.hash));
        assert_eq!(offer.physical_ms, 2);
        assert_eq!(offer.logical, 0);
    }

    #[test]
    fn sync_offer_serialization_roundtrip() {
        let g = genesis("inst-a");
        let log = OpLog::new(g.clone());
        let offer = SyncOffer::from_oplog(&log, 1, 0);

        let bytes = offer.to_bytes();
        let restored = SyncOffer::from_bytes(&bytes).unwrap();
        assert_eq!(restored.heads, offer.heads);
        assert_eq!(restored.physical_ms, offer.physical_ms);
        assert_eq!(restored.logical, offer.logical);
        assert!(restored.bloom.contains(&g.hash));
    }

    // -- entries_missing tests --

    #[test]
    fn entries_missing_detects_delta() {
        // Log A has: genesis → n1 → n2
        // Log B has: genesis → n1
        // B's offer should cause A to send n2.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();

        let mut log_b = OpLog::new(g.clone());
        log_b.append(e1.clone()).unwrap();

        let offer_b = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload = entries_missing(&log_a, &offer_b);

        assert_eq!(payload.entries.len(), 1);
        assert_eq!(payload.entries[0].hash, e2.hash);
        assert!(payload.need.is_empty());
    }

    #[test]
    fn entries_missing_nothing_when_in_sync() {
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();

        let mut log_b = OpLog::new(g.clone());
        log_b.append(e1.clone()).unwrap();

        let offer_b = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload = entries_missing(&log_a, &offer_b);

        assert!(payload.entries.is_empty());
        assert!(payload.need.is_empty());
    }

    #[test]
    fn entries_missing_need_list_for_remote_only() {
        // A has genesis only. B has genesis → n1.
        // A should report that it needs B's head.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-b");

        let log_a = OpLog::new(g.clone());

        let mut log_b = OpLog::new(g.clone());
        log_b.append(e1.clone()).unwrap();

        let offer_b = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload = entries_missing(&log_a, &offer_b);

        // A may send genesis because it can't verify B has it (B's head n1
        // isn't in A's oplog). This is safe — merge ignores duplicates.
        // The essential assertion: A needs B's head (e1).
        assert_eq!(payload.need.len(), 1);
        assert_eq!(payload.need[0], e1.hash);
    }

    #[test]
    fn entries_missing_bloom_reduces_transfer() {
        // Both have genesis + n1. A also has n2.
        // B's bloom should contain genesis + n1, so only n2 is sent.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();

        let mut log_b = OpLog::new(g.clone());
        log_b.append(e1.clone()).unwrap();

        let offer_b = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload = entries_missing(&log_a, &offer_b);

        // Only n2 should be sent (genesis and n1 are in B's bloom).
        assert_eq!(payload.entries.len(), 1);
        assert_eq!(payload.entries[0].hash, e2.hash);
    }

    // -- merge_entries tests --

    #[test]
    fn merge_entries_basic() {
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_b = OpLog::new(g.clone());
        // B doesn't have e1 or e2 yet.
        let merged = merge_entries(&mut log_b, &[e1.clone(), e2.clone()]).unwrap();

        assert_eq!(merged, 2);
        assert_eq!(log_b.len(), 3); // genesis + 2
        assert!(log_b.get(&e1.hash).is_some());
        assert!(log_b.get(&e2.hash).is_some());
    }

    #[test]
    fn merge_entries_out_of_order() {
        // Entries arrive child-first — merge should handle re-ordering.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_b = OpLog::new(g.clone());
        // Send e2 before e1 — e2 depends on e1.
        let merged = merge_entries(&mut log_b, &[e2.clone(), e1.clone()]).unwrap();

        assert_eq!(merged, 2);
        assert_eq!(log_b.len(), 3);
    }

    #[test]
    fn merge_entries_duplicates_ignored() {
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");

        let mut log_b = OpLog::new(g.clone());
        log_b.append(e1.clone()).unwrap();

        // Merge same entry again — should be idempotent.
        let merged = merge_entries(&mut log_b, &[e1.clone()]).unwrap();
        assert_eq!(merged, 0);
        assert_eq!(log_b.len(), 2);
    }

    #[test]
    fn merge_entries_rejects_invalid_hash() {
        let g = genesis("inst-a");
        let mut bad = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        bad.author = "tampered".into(); // hash no longer valid

        let mut log_b = OpLog::new(g.clone());
        let result = merge_entries(&mut log_b, &[bad]);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("invalid hash"));
    }

    // -- Snapshot tests --

    #[test]
    fn snapshot_roundtrip() {
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log = OpLog::new(g.clone());
        log.append(e1.clone()).unwrap();
        log.append(e2.clone()).unwrap();

        let snapshot = Snapshot::from_oplog(&log);
        assert_eq!(snapshot.entries.len(), 3);

        let bytes = snapshot.to_bytes();
        let restored = Snapshot::from_bytes(&bytes).unwrap();
        assert_eq!(restored.entries.len(), 3);
        assert_eq!(restored.entries[0].hash, g.hash);
        assert_eq!(restored.entries[1].hash, e1.hash);
        assert_eq!(restored.entries[2].hash, e2.hash);
    }

    #[test]
    fn snapshot_can_bootstrap_new_peer() {
        // Create log A with some entries, snapshot it, load into fresh log B.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();

        let snapshot = Snapshot::from_oplog(&log_a);

        // Bootstrap a new peer from the snapshot.
        let genesis_entry = &snapshot.entries[0];
        let mut log_b = OpLog::new(genesis_entry.clone());
        let remaining = &snapshot.entries[1..];
        let merged = merge_entries(&mut log_b, remaining).unwrap();

        assert_eq!(merged, 2);
        assert_eq!(log_b.len(), 3);
        assert_eq!(log_b.heads(), log_a.heads());
    }

    // -- Full sync protocol round-trip --

    #[test]
    fn full_sync_roundtrip_a_to_b() {
        // A has entries B doesn't. Sync A → B.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();

        let mut log_b = OpLog::new(g.clone());

        // Step 1: B generates offer.
        let offer_b = SyncOffer::from_oplog(&log_b, 1, 0);

        // Step 2: A computes what B is missing.
        let payload = entries_missing(&log_a, &offer_b);

        // Step 3: B merges the entries.
        let merged = merge_entries(&mut log_b, &payload.entries).unwrap();

        assert_eq!(merged, 2);
        assert_eq!(log_b.len(), 3);
        assert_eq!(log_a.heads(), log_b.heads());
    }

    #[test]
    fn full_sync_bidirectional() {
        // A and B both have unique entries. After bidirectional sync, both converge.
        let g = genesis("inst-a");

        // A: genesis → a1
        let a1 = make_entry(add_node_op("a1"), vec![g.hash], 2, "inst-a");
        let mut log_a = OpLog::new(g.clone());
        log_a.append(a1.clone()).unwrap();

        // B: genesis → b1
        let b1 = make_entry(add_node_op("b1"), vec![g.hash], 2, "inst-b");
        let mut log_b = OpLog::new(g.clone());
        log_b.append(b1.clone()).unwrap();

        // Sync A → B.
        let offer_b = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload_a_to_b = entries_missing(&log_a, &offer_b);
        merge_entries(&mut log_b, &payload_a_to_b.entries).unwrap();

        // Sync B → A.
        let offer_a = SyncOffer::from_oplog(&log_a, 2, 0);
        let payload_b_to_a = entries_missing(&log_b, &offer_a);
        merge_entries(&mut log_a, &payload_b_to_a.entries).unwrap();

        // Both should have genesis + a1 + b1 = 3 entries.
        assert_eq!(log_a.len(), 3);
        assert_eq!(log_b.len(), 3);

        // Both should have the same heads (a1 and b1 — fork).
        let heads_a: HashSet<Hash> = log_a.heads().into_iter().collect();
        let heads_b: HashSet<Hash> = log_b.heads().into_iter().collect();
        assert_eq!(heads_a, heads_b);
        assert!(heads_a.contains(&a1.hash));
        assert!(heads_a.contains(&b1.hash));
    }

    #[test]
    fn entries_missing_forces_heads_despite_bloom_fp() {
        // D-027 fix: if our head is falsely contained in the remote's bloom,
        // it must still be included in the payload (it's not in the remote's
        // heads, so they don't have it).
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();

        // Craft a fake offer where the bloom claims to have ALL of A's entries
        // (simulating false positives), but remote heads are only [e1].
        let mut bloom = BloomFilter::new(128, 0.01);
        bloom.insert(&g.hash);
        bloom.insert(&e1.hash);
        bloom.insert(&e2.hash); // FP: bloom claims remote has e2

        let fake_offer = SyncOffer {
            protocol_version: PROTOCOL_VERSION,
            heads: vec![e1.hash], // remote doesn't actually have e2
            bloom,
            physical_ms: 2,
            logical: 0,
        };

        let payload = entries_missing(&log_a, &fake_offer);

        // e2 must be included — it's our head and not in remote's heads.
        let sent_hashes: HashSet<Hash> = payload.entries.iter().map(|e| e.hash).collect();
        assert!(
            sent_hashes.contains(&e2.hash),
            "head entry must be sent even when bloom falsely contains it"
        );
    }

    #[test]
    fn entries_missing_forces_heads_with_ancestor_closure() {
        // When the bloom FPs both a head AND its parent, the ancestor closure
        // (Phase 2) should recover the parent after Phase 1.5 forces the head.
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");
        let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3, "inst-a");
        let e3 = make_entry(add_node_op("n3"), vec![e2.hash], 4, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();
        log_a.append(e2.clone()).unwrap();
        log_a.append(e3.clone()).unwrap();

        // Bloom FPs everything. Remote only has genesis.
        let mut bloom = BloomFilter::new(128, 0.01);
        for entry in log_a.entries_since(None) {
            bloom.insert(&entry.hash);
        }

        let fake_offer = SyncOffer {
            protocol_version: PROTOCOL_VERSION,
            heads: vec![g.hash],
            bloom,
            physical_ms: 1,
            logical: 0,
        };

        let payload = entries_missing(&log_a, &fake_offer);
        let sent_hashes: HashSet<Hash> = payload.entries.iter().map(|e| e.hash).collect();

        // All non-genesis entries must be sent: e1, e2, e3.
        // Phase 1.5 forces e3 (our head), Phase 2 recovers e2 and e1.
        assert!(
            sent_hashes.contains(&e1.hash),
            "e1 must be recovered by ancestor closure"
        );
        assert!(
            sent_hashes.contains(&e2.hash),
            "e2 must be recovered by ancestor closure"
        );
        assert!(sent_hashes.contains(&e3.hash), "e3 must be forced as head");
    }

    #[test]
    fn sync_is_idempotent() {
        let g = genesis("inst-a");
        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2, "inst-a");

        let mut log_a = OpLog::new(g.clone());
        log_a.append(e1.clone()).unwrap();

        let mut log_b = OpLog::new(g.clone());

        // Sync once.
        let offer_b = SyncOffer::from_oplog(&log_b, 1, 0);
        let payload = entries_missing(&log_a, &offer_b);
        merge_entries(&mut log_b, &payload.entries).unwrap();
        assert_eq!(log_b.len(), 2);

        // Sync again — should be a no-op.
        let offer_b2 = SyncOffer::from_oplog(&log_b, 2, 0);
        let payload2 = entries_missing(&log_a, &offer_b2);
        assert!(payload2.entries.is_empty());
        let merged2 = merge_entries(&mut log_b, &payload2.entries).unwrap();
        assert_eq!(merged2, 0);
        assert_eq!(log_b.len(), 2);
    }
}
