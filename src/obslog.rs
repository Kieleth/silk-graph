//! ObservationLog — append-only, TTL-pruned time-series store (D-025).
//!
//! The "log" half of Silk's log/KG duality (SA-014).
//! Stores raw observations (health checks, metrics, container status).
//! Local-only — never syncs between instances. TTL-pruned — entries older
//! than `max_age_secs` are deleted on truncate().
//!
//! Backed by redb in a separate file from GraphStore.

use std::collections::BTreeMap;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use redb::{Database, ReadableTable, TableDefinition};

/// Observations table: (source, timestamp_ms) → msgpack(value, metadata).
/// Using a regular table with a compound key encoded as bytes.
const OBS_TABLE: TableDefinition<&[u8], &[u8]> = TableDefinition::new("observations");

/// Meta table for bookkeeping.
const OBS_META: TableDefinition<&str, &[u8]> = TableDefinition::new("obs_meta");

/// A single observation record.
#[derive(Debug, Clone)]
pub struct Observation {
    pub timestamp_ms: u64,
    pub source: String,
    pub value: f64,
    pub metadata: BTreeMap<String, String>,
}

/// Serialized form stored in redb value.
#[derive(serde::Serialize, serde::Deserialize)]
struct ObsValue {
    value: f64,
    metadata: BTreeMap<String, String>,
}

/// Compound key: source + timestamp_ms, encoded for lexicographic ordering.
/// Format: [source_len(2 bytes)][source bytes][timestamp_ms(8 bytes BE)]
/// S-13: returns error instead of silently truncating source names > 65535 bytes.
fn encode_key(source: &str, timestamp_ms: u64) -> Result<Vec<u8>, ObsLogError> {
    let src = source.as_bytes();
    if src.len() > u16::MAX as usize {
        return Err(ObsLogError::Io(format!(
            "source name too long: {} bytes (max {})",
            src.len(),
            u16::MAX
        )));
    }
    let mut key = Vec::with_capacity(2 + src.len() + 8);
    key.extend_from_slice(&(src.len() as u16).to_be_bytes());
    key.extend_from_slice(src);
    key.extend_from_slice(&timestamp_ms.to_be_bytes());
    Ok(key)
}

/// Decode source and timestamp from a compound key.
fn decode_key(key: &[u8]) -> Option<(String, u64)> {
    if key.len() < 10 {
        return None;
    }
    let src_len = u16::from_be_bytes([key[0], key[1]]) as usize;
    if key.len() < 2 + src_len + 8 {
        return None;
    }
    let source = String::from_utf8_lossy(&key[2..2 + src_len]).to_string();
    let ts_bytes: [u8; 8] = key[2 + src_len..2 + src_len + 8].try_into().ok()?;
    let timestamp_ms = u64::from_be_bytes(ts_bytes);
    Some((source, timestamp_ms))
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Debug)]
pub enum ObsLogError {
    Io(String),
    Serialization(String),
}

impl std::fmt::Display for ObsLogError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ObsLogError::Io(s) => write!(f, "ObsLog I/O error: {}", s),
            ObsLogError::Serialization(s) => write!(f, "ObsLog serialization error: {}", s),
        }
    }
}

impl std::error::Error for ObsLogError {}

/// Append-only, TTL-pruned observation log backed by redb.
pub struct ObservationLog {
    db: Database,
    pub max_age_secs: u64,
}

impl ObservationLog {
    /// Open or create an observation log at the given path.
    pub fn open(path: &Path, max_age_secs: u64) -> Result<Self, ObsLogError> {
        let db = Database::create(path).map_err(|e| ObsLogError::Io(e.to_string()))?;

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
                .map_err(|e| ObsLogError::Io(e.to_string()))?;
            {
                let _t = txn
                    .open_table(OBS_TABLE)
                    .map_err(|e| ObsLogError::Io(e.to_string()))?;
                let _m = txn
                    .open_table(OBS_META)
                    .map_err(|e| ObsLogError::Io(e.to_string()))?;
            }
            txn.commit().map_err(|e| ObsLogError::Io(e.to_string()))?;
        }

        Ok(Self { db, max_age_secs })
    }

    /// Append a single observation.
    pub fn append(
        &self,
        source: &str,
        value: f64,
        metadata: BTreeMap<String, String>,
    ) -> Result<(), ObsLogError> {
        let ts = now_ms();
        let key = encode_key(source, ts)?;
        let obs = ObsValue { value, metadata };
        let val = rmp_serde::to_vec(&obs).map_err(|e| ObsLogError::Serialization(e.to_string()))?;

        let txn = self
            .db
            .begin_write()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        {
            let mut table = txn
                .open_table(OBS_TABLE)
                .map_err(|e| ObsLogError::Io(e.to_string()))?;
            table
                .insert(key.as_slice(), val.as_slice())
                .map_err(|e| ObsLogError::Io(e.to_string()))?;
        }
        txn.commit().map_err(|e| ObsLogError::Io(e.to_string()))?;
        Ok(())
    }

    /// Append a batch of observations in a single transaction.
    pub fn append_batch(
        &self,
        observations: &[(String, f64, BTreeMap<String, String>)],
    ) -> Result<(), ObsLogError> {
        let ts = now_ms();
        let txn = self
            .db
            .begin_write()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        {
            let mut table = txn
                .open_table(OBS_TABLE)
                .map_err(|e| ObsLogError::Io(e.to_string()))?;
            for (i, (source, value, metadata)) in observations.iter().enumerate() {
                // Offset each by 1ms to ensure unique keys within batch
                let key = encode_key(source, ts + i as u64)?;
                let obs = ObsValue {
                    value: *value,
                    metadata: metadata.clone(),
                };
                let val = rmp_serde::to_vec(&obs)
                    .map_err(|e| ObsLogError::Serialization(e.to_string()))?;
                table
                    .insert(key.as_slice(), val.as_slice())
                    .map_err(|e| ObsLogError::Io(e.to_string()))?;
            }
        }
        txn.commit().map_err(|e| ObsLogError::Io(e.to_string()))?;
        Ok(())
    }

    /// Query observations for a source since a given timestamp.
    pub fn query(&self, source: &str, since_ts_ms: u64) -> Result<Vec<Observation>, ObsLogError> {
        let start = encode_key(source, since_ts_ms)?;
        let end = encode_key(source, u64::MAX)?;

        let txn = self
            .db
            .begin_read()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let table = txn
            .open_table(OBS_TABLE)
            .map_err(|e| ObsLogError::Io(e.to_string()))?;

        let mut results = Vec::new();
        let range = table
            .range(start.as_slice()..=end.as_slice())
            .map_err(|e| ObsLogError::Io(e.to_string()))?;

        for entry in range {
            let (k, v) = entry.map_err(|e| ObsLogError::Io(e.to_string()))?;
            let key_bytes = k.value();
            let val_bytes = v.value();

            if let Some((src, ts)) = decode_key(key_bytes) {
                if src == source {
                    let obs: ObsValue = rmp_serde::from_slice(val_bytes)
                        .map_err(|e| ObsLogError::Serialization(e.to_string()))?;
                    results.push(Observation {
                        timestamp_ms: ts,
                        source: src,
                        value: obs.value,
                        metadata: obs.metadata,
                    });
                }
            }
        }
        Ok(results)
    }

    /// Get the most recent observation for a source.
    pub fn query_latest(&self, source: &str) -> Result<Option<Observation>, ObsLogError> {
        let start = encode_key(source, 0)?;
        let end = encode_key(source, u64::MAX)?;

        let txn = self
            .db
            .begin_read()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let table = txn
            .open_table(OBS_TABLE)
            .map_err(|e| ObsLogError::Io(e.to_string()))?;

        let range = table
            .range(start.as_slice()..=end.as_slice())
            .map_err(|e| ObsLogError::Io(e.to_string()))?;

        let mut latest: Option<Observation> = None;
        for entry in range {
            let (k, v) = entry.map_err(|e| ObsLogError::Io(e.to_string()))?;
            if let Some((src, ts)) = decode_key(k.value()) {
                if src == source {
                    let obs: ObsValue = rmp_serde::from_slice(v.value())
                        .map_err(|e| ObsLogError::Serialization(e.to_string()))?;
                    latest = Some(Observation {
                        timestamp_ms: ts,
                        source: src,
                        value: obs.value,
                        metadata: obs.metadata,
                    });
                }
            }
        }
        Ok(latest)
    }

    /// List distinct sources that have observations.
    pub fn sources(&self) -> Result<Vec<String>, ObsLogError> {
        let txn = self
            .db
            .begin_read()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let table = txn
            .open_table(OBS_TABLE)
            .map_err(|e| ObsLogError::Io(e.to_string()))?;

        let mut seen = std::collections::BTreeSet::new();
        let range = table.iter().map_err(|e| ObsLogError::Io(e.to_string()))?;

        for entry in range {
            let (k, _) = entry.map_err(|e| ObsLogError::Io(e.to_string()))?;
            if let Some((src, _)) = decode_key(k.value()) {
                seen.insert(src);
            }
        }
        Ok(seen.into_iter().collect())
    }

    /// Delete all observations older than `before_ts_ms`. Returns count deleted.
    pub fn truncate(&self, before_ts_ms: u64) -> Result<u64, ObsLogError> {
        let txn = self
            .db
            .begin_write()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let mut deleted = 0u64;
        {
            let mut table = txn
                .open_table(OBS_TABLE)
                .map_err(|e| ObsLogError::Io(e.to_string()))?;

            // Collect keys to delete (can't delete while iterating).
            let mut to_delete = Vec::new();
            {
                let range = table.iter().map_err(|e| ObsLogError::Io(e.to_string()))?;
                for entry in range {
                    let (k, _) = entry.map_err(|e| ObsLogError::Io(e.to_string()))?;
                    if let Some((_, ts)) = decode_key(k.value()) {
                        if ts < before_ts_ms {
                            to_delete.push(k.value().to_vec());
                        }
                    }
                }
            }

            for key in &to_delete {
                table
                    .remove(key.as_slice())
                    .map_err(|e| ObsLogError::Io(e.to_string()))?;
                deleted += 1;
            }
        }
        txn.commit().map_err(|e| ObsLogError::Io(e.to_string()))?;
        Ok(deleted)
    }

    /// Total number of observations.
    pub fn count(&self) -> Result<u64, ObsLogError> {
        let txn = self
            .db
            .begin_read()
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let table = txn
            .open_table(OBS_TABLE)
            .map_err(|e| ObsLogError::Io(e.to_string()))?;
        let mut count = 0u64;
        let iter = table.iter().map_err(|e| ObsLogError::Io(e.to_string()))?;
        for _ in iter {
            count += 1;
        }
        Ok(count)
    }

    /// Size of the redb file in bytes.
    pub fn size_bytes(&self) -> u64 {
        // redb doesn't expose file size directly; we approximate from metadata
        // or use the file system
        0 // Caller should use std::fs::metadata on the path
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread::sleep;
    use std::time::Duration;

    fn temp_log() -> ObservationLog {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test_obs.redb");
        // Leak the dir so it's not cleaned up during test
        let path_owned = path.to_path_buf();
        std::mem::forget(dir);
        ObservationLog::open(&path_owned, 86400).unwrap()
    }

    #[test]
    fn test_append_and_query() {
        let log = temp_log();
        log.append("health.claro", 200.0, BTreeMap::new()).unwrap();
        sleep(Duration::from_millis(2));
        log.append("health.claro", 500.0, BTreeMap::new()).unwrap();

        let all = log.query("health.claro", 0).unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].value, 200.0);
        assert_eq!(all[1].value, 500.0);
    }

    #[test]
    fn test_query_latest() {
        let log = temp_log();
        log.append("metrics.cpu", 45.0, BTreeMap::new()).unwrap();
        sleep(Duration::from_millis(2));
        log.append("metrics.cpu", 67.0, BTreeMap::new()).unwrap();

        let latest = log.query_latest("metrics.cpu").unwrap().unwrap();
        assert_eq!(latest.value, 67.0);
    }

    #[test]
    fn test_query_latest_empty() {
        let log = temp_log();
        assert!(log.query_latest("nonexistent").unwrap().is_none());
    }

    #[test]
    fn test_sources() {
        let log = temp_log();
        log.append("health.claro", 200.0, BTreeMap::new()).unwrap();
        log.append("health.colibri", 200.0, BTreeMap::new())
            .unwrap();
        log.append("metrics.cpu", 45.0, BTreeMap::new()).unwrap();

        let sources = log.sources().unwrap();
        assert_eq!(
            sources,
            vec!["health.claro", "health.colibri", "metrics.cpu"]
        );
    }

    #[test]
    fn test_truncate() {
        let log = temp_log();
        log.append("health.claro", 200.0, BTreeMap::new()).unwrap();
        sleep(Duration::from_millis(50));
        let cutoff = now_ms();
        sleep(Duration::from_millis(50));
        log.append("health.claro", 201.0, BTreeMap::new()).unwrap();

        let deleted = log.truncate(cutoff).unwrap();
        assert_eq!(deleted, 1);

        let remaining = log.query("health.claro", 0).unwrap();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].value, 201.0);
    }

    #[test]
    fn test_count() {
        let log = temp_log();
        assert_eq!(log.count().unwrap(), 0);
        log.append("a", 1.0, BTreeMap::new()).unwrap();
        log.append("b", 2.0, BTreeMap::new()).unwrap();
        assert_eq!(log.count().unwrap(), 2);
    }

    #[test]
    fn test_metadata() {
        let log = temp_log();
        let mut meta = BTreeMap::new();
        meta.insert("status_text".to_string(), "OK".to_string());
        meta.insert("response_ms".to_string(), "45".to_string());
        log.append("health.claro", 200.0, meta).unwrap();

        let obs = log.query_latest("health.claro").unwrap().unwrap();
        assert_eq!(obs.metadata.get("status_text").unwrap(), "OK");
        assert_eq!(obs.metadata.get("response_ms").unwrap(), "45");
    }

    #[test]
    fn test_batch_append() {
        let log = temp_log();
        let batch = vec![
            ("health.a".to_string(), 200.0, BTreeMap::new()),
            ("health.b".to_string(), 200.0, BTreeMap::new()),
            ("metrics.cpu".to_string(), 55.0, BTreeMap::new()),
        ];
        log.append_batch(&batch).unwrap();
        assert_eq!(log.count().unwrap(), 3);
        assert_eq!(log.sources().unwrap().len(), 3);
    }

    #[test]
    fn test_isolation_between_sources() {
        let log = temp_log();
        log.append("health.claro", 200.0, BTreeMap::new()).unwrap();
        log.append("health.colibri", 500.0, BTreeMap::new())
            .unwrap();

        let claro = log.query("health.claro", 0).unwrap();
        assert_eq!(claro.len(), 1);
        assert_eq!(claro[0].value, 200.0);

        let colibri = log.query("health.colibri", 0).unwrap();
        assert_eq!(colibri.len(), 1);
        assert_eq!(colibri[0].value, 500.0);
    }
}
