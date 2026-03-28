//! OperationBuffer — filesystem-backed write-ahead buffer for graph operations.
//!
//! Stores `GraphOp` payloads as JSONL (one JSON object per line). Operations
//! are buffered when the store isn't available (e.g., boot time) and drained
//! into a live store when it becomes available.
//!
//! The buffer stores raw operations, not entries. No hash, no clock, no parents.
//! These are assigned at drain time through the normal store API. This means:
//! - Ontology validation happens at drain time, not buffer time
//! - HLC timestamps reflect drain time, not event time
//! - Subscriptions fire at drain time
//! - No sync participation (buffer is local, pre-store)
//!
//! Callers should store real event timestamps in operation properties
//! (e.g., `{"timestamp_ms": 1711526400000}`) for audit accuracy.

use std::fs;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use crate::entry::GraphOp;

/// Filesystem-backed buffer for graph operations.
pub struct OperationBuffer {
    path: PathBuf,
}

impl OperationBuffer {
    /// Create or open a buffer at the given path.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    /// Append a graph operation to the buffer.
    pub fn append(&self, op: &GraphOp) -> Result<(), String> {
        let json = serde_json::to_string(op).map_err(|e| format!("serialize: {e}"))?;
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| format!("open {}: {e}", self.path.display()))?;
        writeln!(file, "{json}").map_err(|e| format!("write: {e}"))?;
        Ok(())
    }

    /// Read all buffered operations in order.
    pub fn read_all(&self) -> Result<Vec<GraphOp>, String> {
        if !self.path.exists() {
            return Ok(vec![]);
        }
        let file =
            fs::File::open(&self.path).map_err(|e| format!("open {}: {e}", self.path.display()))?;
        let reader = std::io::BufReader::new(file);
        let mut ops = Vec::new();
        for (i, line) in reader.lines().enumerate() {
            let line = line.map_err(|e| format!("read line {}: {e}", i + 1))?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let op: GraphOp =
                serde_json::from_str(trimmed).map_err(|e| format!("parse line {}: {e}", i + 1))?;
            ops.push(op);
        }
        Ok(ops)
    }

    /// Number of buffered operations.
    /// Counts non-empty lines without parsing JSON (O(n) I/O, no deserialization).
    pub fn len(&self) -> usize {
        if !self.path.exists() {
            return 0;
        }
        let file = match fs::File::open(&self.path) {
            Ok(f) => f,
            Err(_) => return 0,
        };
        std::io::BufReader::new(file)
            .lines()
            .map_while(Result::ok)
            .filter(|l| !l.trim().is_empty())
            .count()
    }

    /// Whether the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Clear the buffer (truncate file).
    pub fn clear(&self) -> Result<(), String> {
        if self.path.exists() {
            fs::write(&self.path, b"").map_err(|e| format!("truncate: {e}"))?;
        }
        Ok(())
    }

    /// Path to the buffer file.
    pub fn path(&self) -> &Path {
        &self.path
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    use crate::entry::Value;

    fn tmp_path() -> PathBuf {
        let dir = tempfile::tempdir().unwrap();
        // Leak the dir so it isn't deleted when the test ends.
        // Tests are short-lived; the OS cleans up.
        let path = dir.path().join("buffer.jsonl");
        std::mem::forget(dir);
        path
    }

    #[test]
    fn empty_buffer_reads_empty() {
        let buf = OperationBuffer::new(tmp_path());
        assert!(buf.is_empty());
        assert_eq!(buf.read_all().unwrap(), vec![]);
    }

    #[test]
    fn append_and_read_roundtrip() {
        let path = tmp_path();
        let buf = OperationBuffer::new(&path);

        let op1 = GraphOp::AddNode {
            node_id: "n1".into(),
            node_type: "server".into(),
            subtype: None,
            label: "Server 1".into(),
            properties: BTreeMap::from([("ip".into(), Value::String("10.0.0.1".into()))]),
        };
        let op2 = GraphOp::UpdateProperty {
            entity_id: "n1".into(),
            key: "status".into(),
            value: Value::String("active".into()),
        };

        buf.append(&op1).unwrap();
        buf.append(&op2).unwrap();

        let ops = buf.read_all().unwrap();
        assert_eq!(ops.len(), 2);
        assert_eq!(buf.len(), 2);

        // Verify first op
        match &ops[0] {
            GraphOp::AddNode { node_id, .. } => assert_eq!(node_id, "n1"),
            _ => panic!("expected AddNode"),
        }
        // Verify second op
        match &ops[1] {
            GraphOp::UpdateProperty { entity_id, key, .. } => {
                assert_eq!(entity_id, "n1");
                assert_eq!(key, "status");
            }
            _ => panic!("expected UpdateProperty"),
        }
    }

    #[test]
    fn clear_empties_buffer() {
        let path = tmp_path();
        let buf = OperationBuffer::new(&path);

        buf.append(&GraphOp::RemoveNode {
            node_id: "n1".into(),
        })
        .unwrap();
        assert_eq!(buf.len(), 1);

        buf.clear().unwrap();
        assert!(buf.is_empty());
    }

    #[test]
    fn nonexistent_file_is_empty() {
        let buf = OperationBuffer::new("/tmp/silk_test_nonexistent_buffer.jsonl");
        assert!(buf.is_empty());
        assert_eq!(buf.read_all().unwrap(), vec![]);
    }

    #[test]
    fn all_op_types_roundtrip() {
        let path = tmp_path();
        let buf = OperationBuffer::new(&path);

        let ops = vec![
            GraphOp::AddNode {
                node_id: "n1".into(),
                node_type: "entity".into(),
                subtype: Some("server".into()),
                label: "S".into(),
                properties: BTreeMap::new(),
            },
            GraphOp::AddEdge {
                edge_id: "e1".into(),
                edge_type: "RUNS_ON".into(),
                source_id: "n1".into(),
                target_id: "n2".into(),
                properties: BTreeMap::new(),
            },
            GraphOp::UpdateProperty {
                entity_id: "n1".into(),
                key: "status".into(),
                value: Value::String("active".into()),
            },
            GraphOp::RemoveNode {
                node_id: "n1".into(),
            },
            GraphOp::RemoveEdge {
                edge_id: "e1".into(),
            },
        ];

        for op in &ops {
            buf.append(op).unwrap();
        }

        let read = buf.read_all().unwrap();
        assert_eq!(read.len(), 5);
    }

    #[test]
    fn multiple_appends_are_additive() {
        let path = tmp_path();
        let buf = OperationBuffer::new(&path);

        buf.append(&GraphOp::RemoveNode {
            node_id: "a".into(),
        })
        .unwrap();

        // Reopen (new OperationBuffer instance, same path)
        let buf2 = OperationBuffer::new(&path);
        buf2.append(&GraphOp::RemoveNode {
            node_id: "b".into(),
        })
        .unwrap();

        assert_eq!(buf2.len(), 2);
    }
}
