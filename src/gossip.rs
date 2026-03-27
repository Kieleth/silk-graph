//! R-05: Gossip Peer Selection — logarithmic fan-out for scalable sync.
//!
//! Instead of syncing with all N-1 peers every tick (O(N²)), select
//! `ceil(ln(N) + 1)` random peers per round. Information propagates
//! like gossip — after O(log N) rounds, all peers converge.
//!
//! Research:
//! - Demers et al. (1987) — Epidemic algorithms for replicated database maintenance
//! - Das, Gupta & Motivala (2002) — SWIM protocol (Consul/Serf)
//! - Leitão, Pereira & Rodrigues (2007) — Plumtree (hybrid push/lazy gossip)

use std::collections::BTreeMap;
use std::time::SystemTime;

/// Information about a known peer.
#[derive(Debug, Clone)]
pub struct PeerInfo {
    pub peer_id: String,
    pub address: String,
    pub last_seen_ms: u64,
}

/// Registry of known peers with gossip-based sync target selection.
///
/// The registry is ephemeral — not stored in the graph or oplog.
/// The application manages peer lifecycle (register/unregister).
#[derive(Debug, Default)]
pub struct PeerRegistry {
    peers: BTreeMap<String, PeerInfo>,
    /// Instance ID — mixed into RNG seed to prevent thundering herd (Bug 9).
    instance_seed: u64,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

impl PeerRegistry {
    pub fn new() -> Self {
        Self {
            peers: BTreeMap::new(),
            instance_seed: 0,
        }
    }

    /// Create with an instance ID for RNG seed diversification (prevents thundering herd).
    pub fn with_instance_id(instance_id: &str) -> Self {
        // Simple hash of instance_id for seed mixing
        let mut h: u64 = 0xcbf29ce484222325; // FNV offset basis
        for b in instance_id.bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3); // FNV prime
        }
        Self {
            peers: BTreeMap::new(),
            instance_seed: h,
        }
    }

    /// Register a peer. Overwrites if peer_id already exists.
    pub fn register(&mut self, peer_id: String, address: String) {
        self.peers.insert(
            peer_id.clone(),
            PeerInfo {
                peer_id,
                address,
                last_seen_ms: 0,
            },
        );
    }

    /// Unregister a peer. Returns true if peer existed.
    pub fn unregister(&mut self, peer_id: &str) -> bool {
        self.peers.remove(peer_id).is_some()
    }

    /// List all registered peers.
    pub fn list(&self) -> Vec<&PeerInfo> {
        self.peers.values().collect()
    }

    /// Number of registered peers.
    pub fn len(&self) -> usize {
        self.peers.len()
    }

    pub fn is_empty(&self) -> bool {
        self.peers.is_empty()
    }

    /// Select sync targets for this round.
    ///
    /// Returns `ceil(ln(N) + 1)` peer IDs, chosen pseudo-randomly using
    /// the current timestamp as a seed. For N < 3, returns all peers.
    ///
    /// The selection is deterministic for a given timestamp — two calls
    /// within the same millisecond return the same targets. This is
    /// intentional: callers should call once per tick.
    pub fn select_sync_targets(&self) -> Vec<String> {
        let n = self.peers.len();
        if n == 0 {
            return vec![];
        }
        if n <= 2 {
            return self.peers.keys().cloned().collect();
        }

        let fan_out = ((n as f64).ln() + 1.0).ceil() as usize;
        let fan_out = fan_out.min(n); // can't select more than N

        // Deterministic pseudo-random selection using timestamp seed
        let seed = now_ms() ^ self.instance_seed;
        let peer_ids: Vec<&String> = self.peers.keys().collect();
        let mut selected = Vec::with_capacity(fan_out);
        let mut state = seed;

        while selected.len() < fan_out {
            // LCG: simple, fast, deterministic
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            let idx = (state >> 33) as usize % n;
            let candidate = peer_ids[idx].clone();
            if !selected.contains(&candidate) {
                selected.push(candidate);
            }
        }

        selected
    }

    /// Record that a sync with a peer happened now.
    pub fn record_sync(&mut self, peer_id: &str) {
        if let Some(peer) = self.peers.get_mut(peer_id) {
            peer.last_seen_ms = now_ms();
        }
    }

    /// Get a peer by ID.
    pub fn get(&self, peer_id: &str) -> Option<&PeerInfo> {
        self.peers.get(peer_id)
    }

    /// Check if compaction is safe: all known peers must have synced
    /// since `latest_entry_ms` (the physical clock of the most recent entry).
    /// Returns (safe, reasons) where reasons lists peers that haven't synced.
    pub fn verify_compaction_safe(&self, latest_entry_ms: u64) -> (bool, Vec<String>) {
        if self.peers.is_empty() {
            // No known peers — compaction is trivially safe (single-node system)
            return (true, vec![]);
        }

        let mut reasons = Vec::new();
        for peer in self.peers.values() {
            if peer.last_seen_ms < latest_entry_ms {
                if peer.last_seen_ms == 0 {
                    reasons.push(format!("peer '{}' has never synced", peer.peer_id));
                } else {
                    reasons.push(format!(
                        "peer '{}' last synced at {}ms, but latest entry is at {}ms",
                        peer.peer_id, peer.last_seen_ms, latest_entry_ms
                    ));
                }
            }
        }

        (reasons.is_empty(), reasons)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_and_list() {
        let mut reg = PeerRegistry::new();
        reg.register("a".into(), "tcp://a:7701".into());
        reg.register("b".into(), "tcp://b:7701".into());
        assert_eq!(reg.len(), 2);
        assert!(!reg.is_empty());
    }

    #[test]
    fn unregister_returns_true_if_existed() {
        let mut reg = PeerRegistry::new();
        reg.register("a".into(), "tcp://a:7701".into());
        assert!(reg.unregister("a"));
        assert!(!reg.unregister("a")); // already gone
        assert_eq!(reg.len(), 0);
    }

    #[test]
    fn select_empty_returns_empty() {
        let reg = PeerRegistry::new();
        assert!(reg.select_sync_targets().is_empty());
    }

    #[test]
    fn select_one_peer_returns_it() {
        let mut reg = PeerRegistry::new();
        reg.register("only".into(), "tcp://only:7701".into());
        let targets = reg.select_sync_targets();
        assert_eq!(targets, vec!["only"]);
    }

    #[test]
    fn select_two_peers_returns_both() {
        let mut reg = PeerRegistry::new();
        reg.register("a".into(), "tcp://a:7701".into());
        reg.register("b".into(), "tcp://b:7701".into());
        let targets = reg.select_sync_targets();
        assert_eq!(targets.len(), 2);
    }

    #[test]
    fn select_logarithmic_fan_out() {
        let mut reg = PeerRegistry::new();
        for i in 0..1000 {
            reg.register(
                format!("peer-{i}"),
                format!("tcp://10.0.{0}.{1}:7701", i / 256, i % 256),
            );
        }
        let targets = reg.select_sync_targets();
        let expected = ((1000_f64).ln() + 1.0).ceil() as usize;
        assert_eq!(targets.len(), expected); // ~8
                                             // All targets are valid peer IDs
        for t in &targets {
            assert!(reg.get(t).is_some());
        }
        // No duplicates
        let unique: std::collections::HashSet<&String> = targets.iter().collect();
        assert_eq!(unique.len(), targets.len());
    }

    #[test]
    fn record_sync_updates_last_seen() {
        let mut reg = PeerRegistry::new();
        reg.register("a".into(), "tcp://a:7701".into());
        assert_eq!(reg.get("a").unwrap().last_seen_ms, 0);
        reg.record_sync("a");
        assert!(reg.get("a").unwrap().last_seen_ms > 0);
    }

    #[test]
    fn select_targets_are_subset_of_registered() {
        let mut reg = PeerRegistry::new();
        for i in 0..50 {
            reg.register(format!("p{i}"), format!("addr-{i}"));
        }
        let targets = reg.select_sync_targets();
        for t in &targets {
            assert!(reg.get(t).is_some(), "target {t} not in registry");
        }
    }
}
