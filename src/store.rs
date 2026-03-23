use std::path::Path;

use redb::{Database, ReadableTable, TableDefinition};

use crate::entry::Entry;
use crate::oplog::{OpLog, OpLogError};

/// redb table: entry hash (32 bytes) → msgpack-serialized Entry.
const ENTRIES_TABLE: TableDefinition<&[u8], &[u8]> = TableDefinition::new("entries");

/// redb table: "heads" → msgpack-serialized Vec<Hash>.
const META_TABLE: TableDefinition<&str, &[u8]> = TableDefinition::new("meta");

/// Persistent graph store backed by redb + in-memory OpLog.
///
/// On open: loads all entries from redb into the OpLog.
/// On append: writes to both OpLog (in-memory) and redb (on-disk) atomically.
pub struct Store {
    db: Database,
    pub oplog: OpLog,
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
            return Ok(Self { db, oplog });
        }

        // No existing entries — need genesis.
        let genesis = genesis.ok_or(StoreError::NoGenesis)?;
        let oplog = OpLog::new(genesis.clone());

        // Persist genesis.
        let store = Self { db, oplog };
        store.persist_entry(&genesis)?;
        store.persist_heads()?;

        Ok(store)
    }

    /// Append an entry — writes to both OpLog and redb.
    pub fn append(&mut self, entry: Entry) -> Result<bool, StoreError> {
        let inserted = self
            .oplog
            .append(entry.clone())
            .map_err(StoreError::OpLog)?;
        if inserted {
            self.persist_entry(&entry)?;
            self.persist_heads()?;
        }
        Ok(inserted)
    }

    /// Merge a batch of remote entries — writes each to OpLog and redb.
    ///
    /// Handles out-of-order entries by retrying those with missing parents.
    /// Returns the number of new entries merged.
    pub fn merge(&mut self, entries: &[Entry]) -> Result<usize, StoreError> {
        let mut inserted = 0;
        let mut remaining: Vec<&Entry> = entries.iter().collect();
        let mut max_passes = remaining.len() + 1;

        while !remaining.is_empty() && max_passes > 0 {
            let mut next_remaining = Vec::new();
            for entry in &remaining {
                match self.oplog.append((*entry).clone()) {
                    Ok(true) => {
                        self.persist_entry(entry)?;
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

        if inserted > 0 {
            self.persist_heads()?;
        }

        Ok(inserted)
    }

    /// Persist a single entry to redb.
    fn persist_entry(&self, entry: &Entry) -> Result<(), StoreError> {
        let txn = self
            .db
            .begin_write()
            .map_err(|e| StoreError::Io(e.to_string()))?;
        {
            let mut table = txn
                .open_table(ENTRIES_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            let bytes = entry.to_bytes();
            table
                .insert(entry.hash.as_slice(), bytes.as_slice())
                .map_err(|e| StoreError::Io(e.to_string()))?;
        }
        txn.commit().map_err(|e| StoreError::Io(e.to_string()))?;
        Ok(())
    }

    /// Persist current heads to redb meta table.
    fn persist_heads(&self) -> Result<(), StoreError> {
        let heads = self.oplog.heads();
        let bytes = rmp_serde::to_vec(&heads).map_err(|e| StoreError::Io(e.to_string()))?;
        let txn = self
            .db
            .begin_write()
            .map_err(|e| StoreError::Io(e.to_string()))?;
        {
            let mut table = txn
                .open_table(META_TABLE)
                .map_err(|e| StoreError::Io(e.to_string()))?;
            table
                .insert("heads", bytes.as_slice())
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
    /// Finds the genesis (entry with empty `next`), builds the OpLog,
    /// then appends remaining entries in topological order.
    fn reconstruct_oplog(entries: Vec<Entry>) -> Result<OpLog, StoreError> {
        // Find genesis (entry with next=[]).
        let genesis_idx = entries
            .iter()
            .position(|e| e.next.is_empty())
            .ok_or(StoreError::Io("no genesis entry found".into()))?;

        let genesis = entries[genesis_idx].clone();
        let mut oplog = OpLog::new(genesis);

        // Remaining entries need topological ordering.
        // Simple approach: keep trying to append until all are inserted.
        let mut remaining: Vec<Entry> = entries
            .into_iter()
            .enumerate()
            .filter(|(i, _)| *i != genesis_idx)
            .map(|(_, e)| e)
            .collect();

        let mut max_iterations = remaining.len() * remaining.len() + 1;
        while !remaining.is_empty() && max_iterations > 0 {
            let mut next_remaining = Vec::new();
            for entry in remaining {
                match oplog.append(entry.clone()) {
                    Ok(_) => {} // inserted or duplicate
                    Err(OpLogError::MissingParent(_)) => {
                        next_remaining.push(entry); // try later
                    }
                    Err(e) => return Err(StoreError::Io(format!("reconstruct failed: {e}"))),
                }
            }
            remaining = next_remaining;
            max_iterations -= 1;
        }

        if !remaining.is_empty() {
            return Err(StoreError::Io(format!(
                "could not reconstruct oplog: {} entries with unresolvable parents",
                remaining.len()
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
