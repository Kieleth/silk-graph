//! Read-only provenance observation.
//!
//! One primitive: `entries_affecting(id)`. Scans the OpLog and returns every
//! entry whose payload references the given node or edge id, in topological
//! order. Deterministic over OpLog state alone — see PROOF.md Theorem 5.
//!
//! Callers build typed provenance views on top of this. Silk does not bake
//! in a `Provenance` taxonomy; the primitive is the contract.

use std::collections::HashSet;

use crate::entry::{Entry, GraphOp, Hash};
use crate::oplog::OpLog;

impl OpLog {
    /// Return all entries whose payload references the given id (node_id or
    /// edge_id, including edges whose source_id / target_id is `id`) in
    /// topological order.
    ///
    /// Deterministic over OpLog state. Two peers with identical OpLogs return
    /// byte-identical results. See PROOF.md Theorem 5.
    ///
    /// Post-compaction: pre-checkpoint writes are folded into a single
    /// synthetic `Checkpoint` entry. If the checkpoint's embedded ops mention
    /// `id`, the checkpoint entry itself is returned.
    ///
    /// Cost: linear scan over the OpLog. Unmeasured in practice; if this
    /// becomes a hot path, add an id-indexed side table after profiling.
    pub fn entries_affecting(&self, id: &str) -> Vec<&Entry> {
        let mut matching: HashSet<Hash> = HashSet::new();
        for (hash, entry) in self.iter_entries() {
            if payload_mentions_id(&entry.payload, id) {
                matching.insert(*hash);
            }
        }
        self.topo_sort(&matching)
    }
}

/// Does this op reference the given id (as a node_id, edge_id, source_id, or
/// target_id)?  Recurses into Checkpoint ops so post-compaction scans still
/// find matches.
fn payload_mentions_id(op: &GraphOp, id: &str) -> bool {
    match op {
        GraphOp::AddNode { node_id, .. } => node_id == id,
        GraphOp::AddEdge {
            edge_id,
            source_id,
            target_id,
            ..
        } => edge_id == id || source_id == id || target_id == id,
        GraphOp::UpdateProperty { entity_id, .. } => entity_id == id,
        GraphOp::RemoveNode { node_id } => node_id == id,
        GraphOp::RemoveEdge { edge_id } => edge_id == id,
        GraphOp::DefineOntology { .. } => false,
        GraphOp::ExtendOntology { .. } => false,
        GraphOp::DefineLens { .. } => false,
        GraphOp::Checkpoint { ops, .. } => ops.iter().any(|inner| payload_mentions_id(inner, id)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::LamportClock;
    use crate::entry::{Entry, Value};
    use crate::ontology::Ontology;

    fn mk_genesis() -> Entry {
        Entry::new(
            GraphOp::DefineOntology {
                ontology: Ontology {
                    node_types: Default::default(),
                    edge_types: Default::default(),
                },
            },
            Vec::new(),
            Vec::new(),
            LamportClock::new("test-peer".to_string()),
            "test-peer".to_string(),
        )
    }

    fn mk_entry(op: GraphOp, parents: Vec<Hash>, clock: LamportClock) -> Entry {
        Entry::new(op, parents, Vec::new(), clock, "test-peer".to_string())
    }

    fn add_node(id: &str) -> GraphOp {
        GraphOp::AddNode {
            node_id: id.to_string(),
            node_type: "thing".to_string(),
            subtype: None,
            label: id.to_string(),
            properties: Default::default(),
        }
    }

    fn update_prop(id: &str, key: &str, value: &str) -> GraphOp {
        GraphOp::UpdateProperty {
            entity_id: id.to_string(),
            key: key.to_string(),
            value: Value::String(value.to_string()),
        }
    }

    fn remove_node(id: &str) -> GraphOp {
        GraphOp::RemoveNode {
            node_id: id.to_string(),
        }
    }

    fn add_edge(edge_id: &str, source_id: &str, target_id: &str) -> GraphOp {
        GraphOp::AddEdge {
            edge_id: edge_id.to_string(),
            edge_type: "LINK".to_string(),
            source_id: source_id.to_string(),
            target_id: target_id.to_string(),
            properties: Default::default(),
        }
    }

    /// Test 7: never-existed id returns empty.
    #[test]
    fn never_existed_returns_empty() {
        let log = OpLog::new(mk_genesis());
        assert!(log.entries_affecting("nope").is_empty());
    }

    /// Test 1: single create returns the one entry.
    #[test]
    fn single_create_returns_one_entry() {
        let mut log = OpLog::new(mk_genesis());
        let mut clock = LamportClock::new("test-peer".to_string());
        clock.tick();
        let add = mk_entry(add_node("n1"), vec![log.heads()[0]], clock);
        log.append(add.clone()).unwrap();

        let result = log.entries_affecting("n1");
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].hash, add.hash);
    }

    /// Test 2: many updates to same property return in topo order.
    #[test]
    fn many_updates_return_in_topo_order() {
        let mut log = OpLog::new(mk_genesis());
        let mut clock = LamportClock::new("test-peer".to_string());

        clock.tick();
        let add = mk_entry(add_node("n1"), vec![log.heads()[0]], clock.clone());
        log.append(add.clone()).unwrap();

        clock.tick();
        let u1 = mk_entry(
            update_prop("n1", "name", "foo"),
            vec![add.hash],
            clock.clone(),
        );
        log.append(u1.clone()).unwrap();

        clock.tick();
        let u2 = mk_entry(
            update_prop("n1", "name", "bar"),
            vec![u1.hash],
            clock.clone(),
        );
        log.append(u2.clone()).unwrap();

        let result = log.entries_affecting("n1");
        assert_eq!(result.len(), 3);
        assert_eq!(result[0].hash, add.hash);
        assert_eq!(result[1].hash, u1.hash);
        assert_eq!(result[2].hash, u2.hash);
    }

    /// Test 4: tombstoned node returns create + remove.
    #[test]
    fn tombstoned_node_returns_create_and_remove() {
        let mut log = OpLog::new(mk_genesis());
        let mut clock = LamportClock::new("test-peer".to_string());

        clock.tick();
        let add = mk_entry(add_node("n1"), vec![log.heads()[0]], clock.clone());
        log.append(add.clone()).unwrap();

        clock.tick();
        let rm = mk_entry(remove_node("n1"), vec![add.hash], clock.clone());
        log.append(rm.clone()).unwrap();

        let result = log.entries_affecting("n1");
        assert_eq!(result.len(), 2);
        let hashes: Vec<Hash> = result.iter().map(|e| e.hash).collect();
        assert!(hashes.contains(&add.hash));
        assert!(hashes.contains(&rm.hash));
    }

    /// Test 5: node involved as edge source is found via edge lookup of node id.
    /// Confirms edges whose source/target references the node DO surface.
    #[test]
    fn node_id_finds_edges_where_it_is_source_or_target() {
        let mut log = OpLog::new(mk_genesis());
        let mut clock = LamportClock::new("test-peer".to_string());

        clock.tick();
        let add_a = mk_entry(add_node("a"), vec![log.heads()[0]], clock.clone());
        log.append(add_a.clone()).unwrap();
        clock.tick();
        let add_b = mk_entry(add_node("b"), vec![add_a.hash], clock.clone());
        log.append(add_b.clone()).unwrap();
        clock.tick();
        let edge = mk_entry(add_edge("e1", "a", "b"), vec![add_b.hash], clock.clone());
        log.append(edge.clone()).unwrap();

        let for_a = log.entries_affecting("a");
        let hashes_a: Vec<Hash> = for_a.iter().map(|e| e.hash).collect();
        assert!(hashes_a.contains(&add_a.hash));
        assert!(
            hashes_a.contains(&edge.hash),
            "edge with source=a should surface for node id 'a'"
        );

        let for_b = log.entries_affecting("b");
        let hashes_b: Vec<Hash> = for_b.iter().map(|e| e.hash).collect();
        assert!(hashes_b.contains(&add_b.hash));
        assert!(
            hashes_b.contains(&edge.hash),
            "edge with target=b should surface for node id 'b'"
        );
    }

    /// Test 6: edge id lookup returns AddEdge and subsequent ops.
    #[test]
    fn edge_id_lookup_returns_edge_ops() {
        let mut log = OpLog::new(mk_genesis());
        let mut clock = LamportClock::new("test-peer".to_string());

        clock.tick();
        let add_a = mk_entry(add_node("a"), vec![log.heads()[0]], clock.clone());
        log.append(add_a.clone()).unwrap();
        clock.tick();
        let add_b = mk_entry(add_node("b"), vec![add_a.hash], clock.clone());
        log.append(add_b.clone()).unwrap();
        clock.tick();
        let edge = mk_entry(add_edge("e1", "a", "b"), vec![add_b.hash], clock.clone());
        log.append(edge.clone()).unwrap();
        clock.tick();
        let rm_edge = mk_entry(
            GraphOp::RemoveEdge {
                edge_id: "e1".to_string(),
            },
            vec![edge.hash],
            clock.clone(),
        );
        log.append(rm_edge.clone()).unwrap();

        let result = log.entries_affecting("e1");
        let hashes: Vec<Hash> = result.iter().map(|e| e.hash).collect();
        assert!(hashes.contains(&edge.hash));
        assert!(hashes.contains(&rm_edge.hash));
        assert_eq!(result.len(), 2, "only AddEdge and RemoveEdge reference e1");
    }

    /// Test 10: determinism — same ops on two peers produce identical results.
    /// This validates Theorem 5's CRDT-safety corollary. Clocks fixed to
    /// simulate byte-identical OpLogs on two peers after sync.
    #[test]
    fn determinism_two_peers_identical_results() {
        let build_log = || {
            let genesis = Entry::new(
                GraphOp::DefineOntology {
                    ontology: Ontology {
                        node_types: Default::default(),
                        edge_types: Default::default(),
                    },
                },
                Vec::new(),
                Vec::new(),
                LamportClock::with_values("peer", 1000, 0),
                "peer".to_string(),
            );
            let mut log = OpLog::new(genesis);
            let add = mk_entry(
                add_node("n1"),
                vec![log.heads()[0]],
                LamportClock::with_values("peer", 1001, 0),
            );
            log.append(add.clone()).unwrap();
            let u1 = mk_entry(
                update_prop("n1", "x", "1"),
                vec![add.hash],
                LamportClock::with_values("peer", 1002, 0),
            );
            log.append(u1.clone()).unwrap();
            let u2 = mk_entry(
                update_prop("n1", "y", "2"),
                vec![u1.hash],
                LamportClock::with_values("peer", 1003, 0),
            );
            log.append(u2.clone()).unwrap();
            log
        };

        let log_a = build_log();
        let log_b = build_log();

        let a: Vec<Hash> = log_a
            .entries_affecting("n1")
            .iter()
            .map(|e| e.hash)
            .collect();
        let b: Vec<Hash> = log_b
            .entries_affecting("n1")
            .iter()
            .map(|e| e.hash)
            .collect();
        assert_eq!(a, b, "deterministic output across peers (Theorem 5)");
    }
}
