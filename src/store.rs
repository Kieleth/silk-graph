use std::path::Path;

use redb::{Database, ReadableTable, TableDefinition};

use crate::entry::Entry;
use crate::oplog::{OpLog, OpLogError};

/// redb table: entry hash (32 bytes) → msgpack-serialized Entry.
const ENTRIES_TABLE: TableDefinition<&[u8], &[u8]> = TableDefinition::new("entries");

/// redb table: "heads" → msgpack-serialized Vec<Hash>.
const META_TABLE: TableDefinition<&str, &[u8]> = TableDefinition::new("meta");

/// Flush mode controls when entries are persisted to disk.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FlushMode {
    /// Persist every write immediately (safe, slow — ~1000x overhead).
    /// Each `append()` does a redb commit with fsync.
    Immediate,
    /// Buffer writes in memory, persist on explicit `flush()` (fast, deferred durability).
    /// Entries are in the oplog immediately (read-your-writes) but not on disk until flush.
    /// On crash: entries since last flush are lost. Peers restore them on next sync.
    Deferred,
}

/// Persistent graph store backed by redb + in-memory OpLog.
///
/// On open: loads all entries from redb into the OpLog.
/// On append: writes to OpLog (in-memory) immediately. Persistence depends on `flush_mode`:
/// - `Immediate`: each write persists to redb (safe, slow).
/// - `Deferred`: writes buffer until `flush()` is called (fast, one fsync for N writes).
pub struct Store {
    db: Database,
    pub oplog: OpLog,
    flush_mode: FlushMode,
    /// Entries appended since last flush (Deferred mode only).
    pending: Vec<Entry>,
}

impl Store {
    /// Open or create a store at the given path.
    ///
    /// If the database already exists, all entries are loaded into the OpLog.
    /// If the database is new, a genesis entry must be provided.
    pub fn open(path: &Path, genesis: Option<Entry>) -> Result<Self, StoreError> {
        let db = Database::create(path).map_err(|e| StoreError::Io(e.to_string()))?;

        // S-09: restrict file permissions to owner-only on Unix
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
        }

        // Ensure tables exist.
        {
            let txn = db
                .begin_write()
                .map_err(|e| StoreError::Io(e.to_string()))?;
            {
                let _t = txn
                    .open_table(ENTRIES_TABLE)
                    .map_err(|e| StoreError::Io(e.to_string()))?;
                let _m = txn
                    .open_table(META_TABLE)
                    .map_err(|e| StoreError::Io(e.to_string()))?;
            }
            txn.commit().map_err(|e| StoreError::Io(e.to_string()))?;
        }

        // Try to load existing entries.
        let existing_entries = Self::load_entries(&db)?;

        if !existing_entries.is_empty() {
            // Reconstruct OpLog from stored entries.
            let oplog = Self::reconstruct_oplog(existing_entries)?;
            return Ok(Self {
                db,
                oplog,
                flush_mode: FlushMode::Immediate,
                pending: Vec::new(),
            });
        }

        // No existing entries — need genesis.
        let genesis = genesis.ok_or(StoreError::NoGenesis)?;
        let oplog = OpLog::new(genesis.clone());

        // Persist genesis (single transaction).
        let store = Self {
            db,
            oplog,
            flush_mode: FlushMode::Immediate,
            pending: Vec::new(),
        };
        store.persist_entry_and_heads(&genesis)?;

        Ok(store)
    }

    /// Append an entry — writes to OpLog immediately, persists based on flush_mode.
    pub fn append(&mut self, entry: Entry) -> Result<bool, StoreError> {
        let inserted = self
            .oplog
            .append(entry.clone())
            .map_err(StoreError::OpLog)?;
        if inserted {
            match self.flush_mode {
                FlushMode::Immediate => self.persist_entry_and_heads(&entry)?,
                FlushMode::Deferred => self.pending.push(entry),
            }
        }
        Ok(inserted)
    }

    /// Set the flush mode.
    pub fn set_flush_mode(&mut self, mode: FlushMode) {
        self.flush_mode = mode;
    }

    /// Flush all pending entries to redb in a single transaction.
    /// No-op if no pending entries or if flush_mode is Immediate.
    pub fn flush(&mut self) -> Result<usize, StoreError> {
        if self.pending.is_empty() {
            return Ok(0);
        }
        let count = self.pending.len();
        let entries: Vec<Entry> = self.pending.drain(..).collect();
        self.persist_entries_and_heads(&entries)?;
        Ok(count)
    }

    /// Number of entries pending flush (0 in Immediate mode).
    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }

    /// Merge a batch of remote entries — writes each to OpLog and redb.
    ///
    /// Handles out-of-order entries by retrying those with missing parents.
    /// Returns the number of new entries merged.
    /// Review 4 fix: batches all entry writes + heads into fewer transactions.
    pub fn merge(&mut self, entries: &[Entry]) -> Result<usize, StoreError> {
        let mut inserted = 0;
        let mut new_entries: Vec<Entry> = Vec::new();
        let mut remaining: Vec<&Entry> = entries.iter().collect();
        let mut max_passes = remaining.len() + 1;

        while !remaining.is_empty() && max_passes > 0 {
            let mut next_remaining = Vec::new();
            for entry in &remaining {
                match self.oplog.append((*entry).clone()) {
                    Ok(true) => {
                        new_entries.push((*entry).clone());
                        inserted += 1;
                    }
                    Ok(false) => {
                        // Duplicate — already have it.
                    }
                    Err(crate::oplog::OpLogError::MissingParent(_)) => {
                        next_remaining.push(*entry);
                    }
                    Err(crate::oplog::OpLogError::InvalidHash) => {
                        return Err(StoreError::Io(format!(
                            "invalid hash for entry {}",
                            hex::encode(entry.hash)
                        )));
                    }
                }
            }
            if next_remaining.len() == remaining.len() {
                return Err(StoreError::Io(format!(
                    "{} entries have unresolvable parents",
                    remaining.len()
                )));
            }
            remaining = next_remaining;
            max_passes -= 1;
        }

        if !new_entries.is_empty() {
            match self.flush_mode {
                FlushMode::Immediate => self.persist_entries_and_heads(&new_entries)?,
                FlushMode::Deferred => self.pending.extend(new_entries),
            }
        }

        Ok(inserted)
    }

    /// R-08: Replace entire store with a single checkpoint entry.
    pub fn replace_with_checkpoint(&mut self, checkpoint: Entry) -> Result<(), StoreError> {
        let txn = self
            .db
            .begin_write()
            .map_err(|e| StoreError::Io(e.to_string()))?;
        {
            let mut table = txn
                .open_table(ENTRIES_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            // Collect all existing keys
            let keys: Vec<Vec<u8>> = table
                .iter()
                .map_err(|e| StoreError::Io(e.to_string()))?
                .filter_map(|r| r.ok().map(|(k, _)| k.value().to_vec()))
                .collect();
            for key in keys {
                table
                    .remove(key.as_slice())
                    .map_err(|e| StoreError::Io(e.to_string()))?;
            }
            // Insert checkpoint
            let entry_bytes = checkpoint.to_bytes();
            table
                .insert(checkpoint.hash.as_slice(), entry_bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        {
            let mut meta = txn
                .open_table(META_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            let heads_bytes = rmp_serde::to_vec(&vec![checkpoint.hash])
                .map_err(|e| StoreError::Io(e.to_string()))?;
            meta.insert("heads", heads_bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        txn.commit().map_err(|e| StoreError::Io(e.to_string()))?;

        // Update in-memory oplog
        self.oplog.replace_with_checkpoint(checkpoint);

        Ok(())
    }

    /// Persist a single entry + updated heads in one redb transaction.
    fn persist_entry_and_heads(&self, entry: &Entry) -> Result<(), StoreError> {
        let txn = self
            .db
            .begin_write()
            .map_err(|e| StoreError::Io(e.to_string()))?;
        {
            let mut entries_table = txn
                .open_table(ENTRIES_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            let bytes = entry.to_bytes();
            entries_table
                .insert(entry.hash.as_slice(), bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        {
            let mut meta_table = txn
                .open_table(META_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            let heads = self.oplog.heads();
            let heads_bytes =
                rmp_serde::to_vec(&heads).map_err(|e| StoreError::Io(e.to_string()))?;
            meta_table
                .insert("heads", heads_bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        txn.commit().map_err(|e| StoreError::Io(e.to_string()))?;
        Ok(())
    }

    /// Persist multiple entries + updated heads in one redb transaction.
    fn persist_entries_and_heads(&self, entries: &[Entry]) -> Result<(), StoreError> {
        let txn = self
            .db
            .begin_write()
            .map_err(|e| StoreError::Io(e.to_string()))?;
        {
            let mut entries_table = txn
                .open_table(ENTRIES_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            for entry in entries {
                let bytes = entry.to_bytes();
                entries_table
                    .insert(entry.hash.as_slice(), bytes.as_slice())
                    .map_err(|e| StoreError::Io(e.to_string()))?;
            }
        }
        {
            let mut meta_table = txn
                .open_table(META_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            let heads = self.oplog.heads();
            let heads_bytes =
                rmp_serde::to_vec(&heads).map_err(|e| StoreError::Io(e.to_string()))?;
            meta_table
                .insert("heads", heads_bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        txn.commit().map_err(|e| StoreError::Io(e.to_string()))?;
        Ok(())
    }

    /// Load all entries from redb.
    fn load_entries(db: &Database) -> Result<Vec<Entry>, StoreError> {
        let txn = db.begin_read().map_err(|e| StoreError::Io(e.to_string()))?;
        let table = match txn.open_table(ENTRIES_TABLE) {
            Ok(t) => t,
            Err(_) => return Ok(vec![]),
        };

        let mut entries = Vec::new();
        let iter = table.iter().map_err(|e| StoreError::Io(e.to_string()))?;
        for result in iter {
            let (_, value) = result.map_err(|e| StoreError::Io(e.to_string()))?;
            let entry = Entry::from_bytes(value.value())
                .map_err(|e| StoreError::Io(format!("corrupt entry: {e}")))?;
            entries.push(entry);
        }
        Ok(entries)
    }

    /// Reconstruct an OpLog from a flat list of entries.
    ///
    /// Finds the genesis (entry with empty `next`), topologically sorts
    /// remaining entries by their `next` links, then appends in order.
    /// Review 4 fix: O(n) via topo sort instead of O(n²) retry loop.
    fn reconstruct_oplog(entries: Vec<Entry>) -> Result<OpLog, StoreError> {
        use std::collections::{HashMap, HashSet, VecDeque};

        if entries.is_empty() {
            return Err(StoreError::Io("no entries to reconstruct".into()));
        }

        // Index entries by hash, find all roots (entries with next=[])
        let mut by_hash: HashMap<crate::entry::Hash, Entry> = HashMap::new();
        let mut roots: Vec<Entry> = Vec::new();
        for entry in entries {
            if entry.next.is_empty() {
                roots.push(entry.clone());
            }
            by_hash.insert(entry.hash, entry);
        }

        if roots.is_empty() {
            return Err(StoreError::Io("no genesis entry found".into()));
        }

        // Use the first root as genesis for the OpLog
        // (multi-peer stores may have multiple roots after sync)
        let genesis = roots[0].clone();
        let genesis_hash = genesis.hash;
        let mut oplog = OpLog::new(genesis);

        // Track all root hashes as "resolved"
        let mut resolved: HashSet<crate::entry::Hash> = HashSet::new();
        resolved.insert(genesis_hash);

        // Append additional roots (other peers' genesis entries)
        // These have next=[] and are handled by oplog.append() as Checkpoint entries
        // or accepted as additional roots.
        for root in &roots[1..] {
            resolved.insert(root.hash);
            // These are already handled by the oplog (Checkpoint replace or duplicate skip)
            let _ = oplog.append(root.clone());
        }

        // Build reverse index: parent_hash → children that depend on it
        let mut children_of: HashMap<crate::entry::Hash, Vec<crate::entry::Hash>> = HashMap::new();
        let mut pending_parents: HashMap<crate::entry::Hash, HashSet<crate::entry::Hash>> =
            HashMap::new();

        for (hash, entry) in &by_hash {
            if resolved.contains(hash) {
                continue;
            }
            let parents: HashSet<_> = entry.next.iter().copied().collect();
            pending_parents.insert(*hash, parents.clone());
            for parent in &parents {
                children_of.entry(*parent).or_default().push(*hash);
            }
        }

        // BFS from all resolved roots: process entries whose parents are all resolved
        let mut ready: VecDeque<crate::entry::Hash> = VecDeque::new();

        for root_hash in &resolved {
            if let Some(kids) = children_of.get(root_hash) {
                for kid in kids {
                    if let Some(pp) = pending_parents.get_mut(kid) {
                        pp.remove(root_hash);
                        if pp.is_empty() {
                            ready.push_back(*kid);
                        }
                    }
                }
            }
        }

        while let Some(hash) = ready.pop_front() {
            if let Some(entry) = by_hash.get(&hash) {
                match oplog.append(entry.clone()) {
                    Ok(_) => {}
                    Err(e) => {
                        return Err(StoreError::Io(format!("reconstruct failed: {e}")));
                    }
                }
                // Unblock children that depended on this entry
                if let Some(kids) = children_of.get(&hash) {
                    for kid in kids {
                        if let Some(pp) = pending_parents.get_mut(kid) {
                            pp.remove(&hash);
                            if pp.is_empty() {
                                ready.push_back(*kid);
                            }
                        }
                    }
                }
            }
        }

        // Check for unresolvable entries
        let unresolved: Vec<_> = pending_parents
            .iter()
            .filter(|(_, parents)| !parents.is_empty())
            .collect();
        if !unresolved.is_empty() {
            return Err(StoreError::Io(format!(
                "could not reconstruct oplog: {} entries with unresolvable parents",
                unresolved.len()
            )));
        }

        Ok(oplog)
    }
}

#[derive(Debug)]
pub enum StoreError {
    Io(String),
    NoGenesis,
    OpLog(OpLogError),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::Io(msg) => write!(f, "store I/O error: {msg}"),
            StoreError::NoGenesis => write!(f, "no genesis entry provided for new store"),
            StoreError::OpLog(e) => write!(f, "oplog error: {e}"),
        }
    }
}

impl std::error::Error for StoreError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::LamportClock;
    use crate::entry::{GraphOp, Hash};
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
                    parent_type: None,
                },
            )]),
            edge_types: BTreeMap::new(),
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

    #[test]
    fn open_creates_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.redb");
        assert!(!path.exists());

        let store = Store::open(&path, Some(genesis())).unwrap();
        assert!(path.exists());
        assert_eq!(store.oplog.len(), 1);
    }

    #[test]
    fn open_existing_loads_state() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.redb");
        let g = genesis();

        // Create store, append entries.
        {
            let mut store = Store::open(&path, Some(g.clone())).unwrap();
            let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2);
            let e2 = make_entry(add_node_op("n2"), vec![e1.hash], 3);
            store.append(e1).unwrap();
            store.append(e2).unwrap();
            assert_eq!(store.oplog.len(), 3);
        }

        // Reopen — should have the same state.
        {
            let store = Store::open(&path, None).unwrap();
            assert_eq!(store.oplog.len(), 3);
            let heads = store.oplog.heads();
            assert_eq!(heads.len(), 1);
        }
    }

    #[test]
    fn new_store_without_genesis_fails() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.redb");
        match Store::open(&path, None) {
            Err(StoreError::NoGenesis) => {} // expected
            Ok(_) => panic!("expected NoGenesis error, got Ok"),
            Err(e) => panic!("expected NoGenesis, got {e}"),
        }
    }

    #[test]
    fn append_persists_across_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.redb");
        let g = genesis();

        let e1 = make_entry(add_node_op("n1"), vec![g.hash], 2);
        let e1_hash = e1.hash;

        {
            let mut store = Store::open(&path, Some(g.clone())).unwrap();
            store.append(e1).unwrap();
        }

        {
            let store = Store::open(&path, None).unwrap();
            assert_eq!(store.oplog.len(), 2);
            assert!(store.oplog.get(&e1_hash).is_some());
        }
    }

    #[test]
    fn concurrent_readers_ok() {
        use std::thread;

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.redb");
        let g = genesis();

        let mut store = Store::open(&path, Some(g.clone())).unwrap();
        for i in 0..10 {
            let next = store.oplog.heads();
            let e = make_entry(add_node_op(&format!("n{i}")), next, (i + 2) as u64);
            store.append(e).unwrap();
        }

        // Multiple scoped threads reading via begin_read() on the shared Database.
        thread::scope(|s| {
            for _ in 0..4 {
                s.spawn(|| {
                    let txn = store.db.begin_read().unwrap();
                    let table = txn.open_table(ENTRIES_TABLE).unwrap();
                    let count = table.iter().unwrap().count();
                    assert_eq!(count, 11); // genesis + 10
                });
            }
        });
    }
}
