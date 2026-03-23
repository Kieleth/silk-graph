use std::collections::{HashMap, HashSet, VecDeque};

use crate::entry::{Entry, GraphOp, Hash};

/// In-memory Merkle-DAG operation log.
///
/// Append-only: entries are content-addressed and linked to their causal
/// predecessors (the heads at time of write). The OpLog tracks heads,
/// supports delta computation (`entries_since`), and topological sorting.
pub struct OpLog {
    /// All entries indexed by hash.
    entries: HashMap<Hash, Entry>,
    /// Current DAG heads — entries with no successors.
    heads: HashSet<Hash>,
    /// Reverse index: hash → set of entries that reference it via `next`.
    /// Used for traversal and head tracking.
    children: HashMap<Hash, HashSet<Hash>>,
    /// Total entry count (including genesis).
    len: usize,
}

impl OpLog {
    /// Create a new OpLog with a genesis entry.
    pub fn new(genesis: Entry) -> Self {
        let hash = genesis.hash;
        let mut entries = HashMap::new();
        entries.insert(hash, genesis);
        let mut heads = HashSet::new();
        heads.insert(hash);
        Self {
            entries,
            heads,
            children: HashMap::new(),
            len: 1,
        }
    }

    /// Append an entry to the log.
    ///
    /// - Verifies the entry hash is valid.
    /// - If the entry already exists (duplicate), returns false.
    /// - Updates heads: the entry's `next` links are no longer heads (they have a successor).
    /// - Returns true if the entry was newly inserted.
    pub fn append(&mut self, entry: Entry) -> Result<bool, OpLogError> {
        if !entry.verify_hash() {
            return Err(OpLogError::InvalidHash);
        }

        // Duplicate — idempotent, no error.
        if self.entries.contains_key(&entry.hash) {
            return Ok(false);
        }

        // Bug 7 fix: if this is a Checkpoint entry (next=[]) arriving at a non-empty
        // oplog, replace the oplog instead of creating a second root.
        if entry.next.is_empty()
            && !self.entries.is_empty()
            && matches!(entry.payload, GraphOp::Checkpoint { .. })
        {
            self.replace_with_checkpoint(entry);
            return Ok(true);
        }

        // All causal predecessors must exist (except for genesis which has next=[]).
        for parent_hash in &entry.next {
            if !self.entries.contains_key(parent_hash) {
                return Err(OpLogError::MissingParent(hex::encode(parent_hash)));
            }
        }

        let hash = entry.hash;

        // Update heads: parents are no longer heads (this entry succeeds them).
        for parent_hash in &entry.next {
            self.heads.remove(parent_hash);
            self.children.entry(*parent_hash).or_default().insert(hash);
        }

        // The new entry is a head (no successors yet).
        self.heads.insert(hash);
        self.entries.insert(hash, entry);
        self.len += 1;

        Ok(true)
    }

    /// Current DAG head hashes.
    pub fn heads(&self) -> Vec<Hash> {
        self.heads.iter().copied().collect()
    }

    /// Get an entry by hash.
    pub fn get(&self, hash: &Hash) -> Option<&Entry> {
        self.entries.get(hash)
    }

    /// Total entries in the log.
    pub fn len(&self) -> usize {
        self.len
    }

    /// Whether the log is empty (should never be — always has genesis).
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Return all entries reachable from current heads that are NOT
    /// reachable from (or equal to) `known_hash`.
    ///
    /// This computes the delta a peer needs: "give me everything you have
    /// that I don't, given that I already have `known_hash` and its ancestors."
    ///
    /// If `known_hash` is None, returns all entries (the entire log).
    pub fn entries_since(&self, known_hash: Option<&Hash>) -> Vec<&Entry> {
        // Collect ALL entries reachable from heads via BFS backwards through `next` links.
        let all_from_heads = self.reachable_from(&self.heads.iter().copied().collect::<Vec<_>>());

        match known_hash {
            None => {
                // No known hash — return everything in topological order.
                self.topo_sort(&all_from_heads)
            }
            Some(kh) => {
                // Find everything reachable from known_hash (what the peer already has).
                let known_set = self.reachable_from(&[*kh]);
                // Delta = all - known.
                let delta: HashSet<Hash> = all_from_heads.difference(&known_set).copied().collect();
                self.topo_sort(&delta)
            }
        }
    }

    /// Topological sort of the given set of entry hashes.
    /// Returns entries in causal order: parents before children.
    pub fn topo_sort(&self, hashes: &HashSet<Hash>) -> Vec<&Entry> {
        // Kahn's algorithm on the subset.
        let mut in_degree: HashMap<Hash, usize> = HashMap::new();
        for &h in hashes {
            let entry = &self.entries[&h];
            let deg = entry.next.iter().filter(|p| hashes.contains(*p)).count();
            in_degree.insert(h, deg);
        }

        let mut queue: VecDeque<Hash> = in_degree
            .iter()
            .filter(|(_, &deg)| deg == 0)
            .map(|(&h, _)| h)
            .collect();

        // Sort the queue for determinism (by Lamport time, then hash).
        let mut sorted_queue: Vec<Hash> = queue.drain(..).collect();
        sorted_queue.sort_by(|a, b| {
            let ea = &self.entries[a];
            let eb = &self.entries[b];
            ea.clock
                .as_tuple()
                .cmp(&eb.clock.as_tuple())
                .then_with(|| a.cmp(b))
        });
        queue = sorted_queue.into();

        let mut result = Vec::new();
        while let Some(h) = queue.pop_front() {
            result.push(&self.entries[&h]);
            // Find children of h that are in our subset.
            if let Some(ch) = self.children.get(&h) {
                let mut ready = Vec::new();
                for &child in ch {
                    if !hashes.contains(&child) {
                        continue;
                    }
                    if let Some(deg) = in_degree.get_mut(&child) {
                        *deg -= 1;
                        if *deg == 0 {
                            ready.push(child);
                        }
                    }
                }
                // Sort for determinism.
                ready.sort_by(|a, b| {
                    let ea = &self.entries[a];
                    let eb = &self.entries[b];
                    ea.clock
                        .as_tuple()
                        .cmp(&eb.clock.as_tuple())
                        .then_with(|| a.cmp(b))
                });
                for r in ready {
                    queue.push_back(r);
                }
            }
        }

        result
    }

    /// R-06: Get all entries with clock <= cutoff, in topological order.
    /// Returns a historical snapshot of the state at the given time.
    pub fn entries_as_of(&self, cutoff_physical: u64, cutoff_logical: u32) -> Vec<&Entry> {
        let cutoff = (cutoff_physical, cutoff_logical);
        let filtered: HashSet<Hash> = self
            .entries
            .iter()
            .filter(|(_, e)| e.clock.as_tuple() <= cutoff)
            .map(|(h, _)| *h)
            .collect();
        self.topo_sort(&filtered)
    }

    /// R-08: Replace entire oplog with a single checkpoint entry.
    /// All previous entries are removed. The checkpoint becomes the sole entry.
    /// SAFETY: Only call after verifying ALL peers have synced past all current entries.
    pub fn replace_with_checkpoint(&mut self, checkpoint: Entry) {
        self.entries.clear();
        self.heads.clear();
        self.children.clear();
        let hash = checkpoint.hash;
        self.entries.insert(hash, checkpoint);
        self.heads.insert(hash);
        self.len = 1;
    }

    /// BFS backwards through `next` links from the given starting hashes.
    /// Returns the set of all reachable hashes (including the starting ones).
    fn reachable_from(&self, starts: &[Hash]) -> HashSet<Hash> {
        let mut visited = HashSet::new();
        let mut queue: VecDeque<Hash> = starts.iter().copied().collect();
        while let Some(h) = queue.pop_front() {
            if !visited.insert(h) {
                continue;
            }
            if let Some(entry) = self.entries.get(&h) {
                for parent in &entry.next {
                    if !visited.contains(parent) {
                        queue.push_back(*parent);
                    }
                }
                // Also follow refs (skip-list) for completeness.
                for r in &entry.refs {
                    if !visited.contains(r) {
                        queue.push_back(*r);
                    }
                }
            }
        }
        visited
    }
}

/// Errors from OpLog operations.
#[derive(Debug, PartialEq)]
pub enum OpLogError {
    InvalidHash,
    MissingParent(String),
}

impl std::fmt::Display for OpLogError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpLogError::InvalidHash => write!(f, "entry hash verification failed"),
            OpLogError::MissingParent(h) => write!(f, "missing parent entry: {h}"),
        }
    }
}

impl std::error::Error for OpLogError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::LamportClock;
    use crate::entry::GraphOp;
    use crate::ontology::{EdgeTypeDef, NodeTypeDef, Ontology};
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
            edge_types: BTreeMap::from([(
                "LINKS".into(),
                EdgeTypeDef {
                    description: None,
                    source_types: vec!["entity".into()],
                    target_types: vec!["entity".into()],
                    properties: BTreeMap::new(),
                },
            )]),
        }
    }

    fn genesis() -> Entry {
        Entry::new(
            GraphOp::DefineOntology {
                ontology: test_ontology(),
            },
            vec![],
            vec![],
            LamportClock::new("test"),
            "test",
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

    fn make_entry(op: GraphOp, next: Vec<Hash>, clock_time: u64) -> Entry {
        Entry::new(
            op,
            next,
            vec![],
            LamportClock::with_values("test", clock_time, 0),
            "test",
        )
    }

    // -----------------------------------------------------------------------
    // test_oplog.rs spec from docs/silk.md
    // -----------------------------------------------------------------------

    #[test]
    fn append_single_entry() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());
        assert_eq!(log.len(), 1);
        assert_eq!(log.heads().len(), 1);
        assert_eq!(log.heads()[0], g.hash);

        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2);
        assert!(log.append(e1.clone()).unwrap());
        assert_eq!(log.len(), 2);
        assert_eq!(log.heads().len(), 1);
        assert_eq!(log.heads()[0], e1.hash);
    }

    #[test]
    fn append_chain() {
        // A → B → C, one head (C)
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        let b = make_entry(add_node_op("b"), vec![a.hash], 3);
        let c = make_entry(add_node_op("c"), vec![b.hash], 4);

        log.append(a).unwrap();
        log.append(b).unwrap();
        log.append(c.clone()).unwrap();

        assert_eq!(log.len(), 4); // genesis + 3
        assert_eq!(log.heads().len(), 1);
        assert_eq!(log.heads()[0], c.hash);
    }

    #[test]
    fn append_fork() {
        // G → A → B, G → A → C → two heads (B, C)
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        log.append(a.clone()).unwrap();

        let b = make_entry(add_node_op("b"), vec![a.hash], 3);
        let c = make_entry(add_node_op("c"), vec![a.hash], 3);
        log.append(b.clone()).unwrap();
        log.append(c.clone()).unwrap();

        assert_eq!(log.len(), 4);
        let heads = log.heads();
        assert_eq!(heads.len(), 2);
        assert!(heads.contains(&b.hash));
        assert!(heads.contains(&c.hash));
    }

    #[test]
    fn append_merge() {
        // Fork then merge → one head
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        log.append(a.clone()).unwrap();

        let b = make_entry(add_node_op("b"), vec![a.hash], 3);
        let c = make_entry(add_node_op("c"), vec![a.hash], 3);
        log.append(b.clone()).unwrap();
        log.append(c.clone()).unwrap();
        assert_eq!(log.heads().len(), 2);

        // Merge: D points to both B and C
        let d = make_entry(add_node_op("d"), vec![b.hash, c.hash], 4);
        log.append(d.clone()).unwrap();

        assert_eq!(log.heads().len(), 1);
        assert_eq!(log.heads()[0], d.hash);
    }

    #[test]
    fn heads_updated_on_append() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());
        assert!(log.heads().contains(&g.hash));

        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2);
        log.append(e1.clone()).unwrap();
        assert!(!log.heads().contains(&g.hash));
        assert!(log.heads().contains(&e1.hash));
    }

    #[test]
    fn entries_since_returns_delta() {
        // G → A → B → C
        // entries_since(A) should return [B, C]
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        let b = make_entry(add_node_op("b"), vec![a.hash], 3);
        let c = make_entry(add_node_op("c"), vec![b.hash], 4);

        log.append(a.clone()).unwrap();
        log.append(b.clone()).unwrap();
        log.append(c.clone()).unwrap();

        let delta = log.entries_since(Some(&a.hash));
        let delta_hashes: Vec<Hash> = delta.iter().map(|e| e.hash).collect();
        assert_eq!(delta_hashes.len(), 2);
        assert!(delta_hashes.contains(&b.hash));
        assert!(delta_hashes.contains(&c.hash));
        // Must be in causal order: B before C
        assert_eq!(delta_hashes[0], b.hash);
        assert_eq!(delta_hashes[1], c.hash);
    }

    #[test]
    fn entries_since_empty_returns_all() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());
        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        log.append(a).unwrap();

        let all = log.entries_since(None);
        assert_eq!(all.len(), 2); // genesis + a
    }

    #[test]
    fn topological_sort_respects_causality() {
        // G → A → B, G → A → C → D (merge B+D)
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let a = make_entry(add_node_op("a"), vec![g.hash], 2);
        log.append(a.clone()).unwrap();
        let b = make_entry(add_node_op("b"), vec![a.hash], 3);
        let c = make_entry(add_node_op("c"), vec![a.hash], 4);
        log.append(b.clone()).unwrap();
        log.append(c.clone()).unwrap();

        let all = log.entries_since(None);
        // Genesis must come first, then A, then B and C in some order
        assert_eq!(all[0].hash, g.hash);
        assert_eq!(all[1].hash, a.hash);
        // B and C can be in either order, but both after A
        let last_two: HashSet<Hash> = all[2..].iter().map(|e| e.hash).collect();
        assert!(last_two.contains(&b.hash));
        assert!(last_two.contains(&c.hash));
    }

    #[test]
    fn duplicate_entry_ignored() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());

        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2);
        assert!(log.append(e1.clone()).unwrap()); // first time → true
        assert!(!log.append(e1.clone()).unwrap()); // duplicate → false
        assert_eq!(log.len(), 2); // still 2
    }

    #[test]
    fn entry_not_found_error() {
        let g = genesis();
        let log = OpLog::new(g.clone());
        let fake_hash = [0xffu8; 32];
        assert!(log.get(&fake_hash).is_none());
    }

    #[test]
    fn invalid_hash_rejected() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());
        let mut bad = make_entry(add_node_op("n1"), vec![g.hash], 2);
        bad.author = "tampered".into(); // hash no longer matches
        assert_eq!(log.append(bad), Err(OpLogError::InvalidHash));
    }

    #[test]
    fn missing_parent_rejected() {
        let g = genesis();
        let mut log = OpLog::new(g.clone());
        let fake_parent = [0xaau8; 32];
        let bad = make_entry(add_node_op("n1"), vec![fake_parent], 2);
        match log.append(bad) {
            Err(OpLogError::MissingParent(_)) => {} // expected
            other => panic!("expected MissingParent, got {:?}", other),
        }
    }
}
