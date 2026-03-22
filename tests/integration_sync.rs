//! Level 3 integration tests — multi-store sync scenarios.
//!
//! These test the sync protocol under realistic distributed conditions:
//! partitions, concurrent writes, heal-and-converge cycles.

use std::collections::{BTreeMap, HashSet};

use silk::clock::LamportClock;
use silk::entry::{Entry, GraphOp, Hash, Value};
use silk::graph::MaterializedGraph;
use silk::ontology::{EdgeTypeDef, NodeTypeDef, Ontology};
use silk::oplog::OpLog;
use silk::sync::{entries_missing, merge_entries, Snapshot, SyncOffer};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn test_ontology() -> Ontology {
    Ontology {
        node_types: BTreeMap::from([
            (
                "entity".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            ),
            (
                "signal".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            ),
        ]),
        edge_types: BTreeMap::from([(
            "CONNECTS".into(),
            EdgeTypeDef {
                description: None,
                source_types: vec!["entity".into()],
                target_types: vec!["entity".into()],
                properties: BTreeMap::new(),
            },
        )]),
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

/// A test peer: op log + materialized graph + clock.
struct Peer {
    oplog: OpLog,
    graph: MaterializedGraph,
    clock: LamportClock,
}

impl Peer {
    fn new(id: &str, g: &Entry) -> Self {
        let mut graph = MaterializedGraph::new(test_ontology());
        graph.apply(g);
        Self {
            oplog: OpLog::new(g.clone()),
            graph,
            clock: LamportClock::new(id),
        }
    }

    fn add_node(&mut self, node_id: &str, props: BTreeMap<String, Value>) -> Hash {
        self.clock.tick();
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: node_id.into(),
                node_type: "entity".into(),
                label: node_id.into(),
                properties: props,
                subtype: None,
            },
            self.oplog.heads(),
            vec![],
            self.clock.clone(),
            &self.clock.id,
        );
        let hash = entry.hash;
        self.graph.apply(&entry);
        self.oplog.append(entry).unwrap();
        hash
    }

    fn add_edge(&mut self, edge_id: &str, src: &str, tgt: &str) -> Hash {
        self.clock.tick();
        let entry = Entry::new(
            GraphOp::AddEdge {
                edge_id: edge_id.into(),
                edge_type: "CONNECTS".into(),
                source_id: src.into(),
                target_id: tgt.into(),
                properties: BTreeMap::new(),
            },
            self.oplog.heads(),
            vec![],
            self.clock.clone(),
            &self.clock.id,
        );
        let hash = entry.hash;
        self.graph.apply(&entry);
        self.oplog.append(entry).unwrap();
        hash
    }

    fn update_property(&mut self, entity_id: &str, key: &str, value: Value) -> Hash {
        self.clock.tick();
        let entry = Entry::new(
            GraphOp::UpdateProperty {
                entity_id: entity_id.into(),
                key: key.into(),
                value,
            },
            self.oplog.heads(),
            vec![],
            self.clock.clone(),
            &self.clock.id,
        );
        let hash = entry.hash;
        self.graph.apply(&entry);
        self.oplog.append(entry).unwrap();
        hash
    }

    fn remove_node(&mut self, node_id: &str) -> Hash {
        self.clock.tick();
        let entry = Entry::new(
            GraphOp::RemoveNode {
                node_id: node_id.into(),
            },
            self.oplog.heads(),
            vec![],
            self.clock.clone(),
            &self.clock.id,
        );
        let hash = entry.hash;
        self.graph.apply(&entry);
        self.oplog.append(entry).unwrap();
        hash
    }

    fn len(&self) -> usize {
        self.oplog.len()
    }

    fn heads(&self) -> Vec<Hash> {
        self.oplog.heads()
    }

    /// One-way sync: push our entries to `other`.
    fn sync_to(&self, other: &mut Peer) {
        let offer = SyncOffer::from_oplog(&other.oplog, other.clock.time);
        let payload = entries_missing(&self.oplog, &offer);
        if !payload.entries.is_empty() {
            let merged = merge_entries(&mut other.oplog, &payload.entries).unwrap();
            // Rematerialize graph for new entries.
            if merged > 0 {
                other.rebuild_graph();
                // Merge clock.
                let max_remote = payload
                    .entries
                    .iter()
                    .map(|e| e.clock.time)
                    .max()
                    .unwrap_or(0);
                other.clock.merge(max_remote);
            }
        }
    }

    /// Bidirectional sync between self and other.
    fn sync_bidi(a: &mut Peer, b: &mut Peer) {
        // A → B
        let offer_b = SyncOffer::from_oplog(&b.oplog, b.clock.time);
        let payload_for_b = entries_missing(&a.oplog, &offer_b);
        if !payload_for_b.entries.is_empty() {
            let merged = merge_entries(&mut b.oplog, &payload_for_b.entries).unwrap();
            if merged > 0 {
                b.rebuild_graph();
                let max_t = payload_for_b
                    .entries
                    .iter()
                    .map(|e| e.clock.time)
                    .max()
                    .unwrap_or(0);
                b.clock.merge(max_t);
            }
        }
        // B → A
        let offer_a = SyncOffer::from_oplog(&a.oplog, a.clock.time);
        let payload_for_a = entries_missing(&b.oplog, &offer_a);
        if !payload_for_a.entries.is_empty() {
            let merged = merge_entries(&mut a.oplog, &payload_for_a.entries).unwrap();
            if merged > 0 {
                a.rebuild_graph();
                let max_t = payload_for_a
                    .entries
                    .iter()
                    .map(|e| e.clock.time)
                    .max()
                    .unwrap_or(0);
                a.clock.merge(max_t);
            }
        }
    }

    fn rebuild_graph(&mut self) {
        let all = self.oplog.entries_since(None);
        let refs: Vec<&Entry> = all.iter().copied().collect();
        self.graph.rebuild(&refs);
    }

    fn live_node_ids(&self) -> HashSet<String> {
        self.graph
            .all_nodes()
            .iter()
            .map(|n| n.node_id.clone())
            .collect()
    }

    fn live_edge_ids(&self) -> HashSet<String> {
        self.graph
            .all_edges()
            .iter()
            .map(|e| e.edge_id.clone())
            .collect()
    }
}

// ===========================================================================
// Test: Partition → Diverge → Heal → Converge
// ===========================================================================

#[test]
fn partition_heal_three_peers() {
    // Setup: 3 peers (A, B, C) all start with the same genesis.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);
    let mut b = Peer::new("inst-b", &g);
    let mut c = Peer::new("inst-c", &g);

    // Phase 1: All connected. A writes, syncs to B and C.
    a.add_node("shared-1", BTreeMap::new());
    a.sync_to(&mut b);
    a.sync_to(&mut c);
    assert_eq!(a.len(), b.len());
    assert_eq!(a.len(), c.len());

    // Phase 2: PARTITION. A↔B can talk. C is isolated.
    // A writes.
    a.add_node("from-a", BTreeMap::new());
    Peer::sync_bidi(&mut a, &mut b);
    // B writes.
    b.add_node("from-b", BTreeMap::new());
    Peer::sync_bidi(&mut a, &mut b);
    // C writes independently (isolated).
    c.add_node("from-c", BTreeMap::new());

    // A and B should have shared-1, from-a, from-b. NOT from-c.
    assert!(a.graph.get_node("from-a").is_some());
    assert!(a.graph.get_node("from-b").is_some());
    assert!(a.graph.get_node("from-c").is_none());
    assert!(b.graph.get_node("from-a").is_some());
    assert!(b.graph.get_node("from-b").is_some());
    // C should have shared-1, from-c. NOT from-a, from-b.
    assert!(c.graph.get_node("from-c").is_some());
    assert!(c.graph.get_node("from-a").is_none());

    // Phase 3: HEAL. All three sync bidirectionally.
    Peer::sync_bidi(&mut a, &mut c);
    Peer::sync_bidi(&mut b, &mut c);
    // Second round to propagate anything C got from A to B and vice versa.
    Peer::sync_bidi(&mut a, &mut b);

    // Phase 4: VERIFY CONVERGENCE.
    // All three should have the same nodes.
    let expected_nodes: HashSet<String> = ["shared-1", "from-a", "from-b", "from-c"]
        .iter()
        .map(|s| s.to_string())
        .collect();

    assert_eq!(a.live_node_ids(), expected_nodes, "A missing nodes");
    assert_eq!(b.live_node_ids(), expected_nodes, "B missing nodes");
    assert_eq!(c.live_node_ids(), expected_nodes, "C missing nodes");

    // All three should have the same heads.
    let heads_a: HashSet<Hash> = a.heads().into_iter().collect();
    let heads_b: HashSet<Hash> = b.heads().into_iter().collect();
    let heads_c: HashSet<Hash> = c.heads().into_iter().collect();
    assert_eq!(heads_a, heads_b, "A and B heads diverge");
    assert_eq!(heads_b, heads_c, "B and C heads diverge");
}

#[test]
fn partition_heal_conflicting_property_updates() {
    // A and C both update the same property during partition.
    // After heal, LWW resolves deterministically.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);
    let mut c = Peer::new("inst-c", &g);

    // Both start with the same node.
    a.add_node("s1", BTreeMap::new());
    a.sync_to(&mut c);

    // PARTITION: A and C update "status" independently.
    a.update_property("s1", "status", Value::String("alive".into()));
    c.update_property("s1", "status", Value::String("dead".into()));

    // HEAL.
    Peer::sync_bidi(&mut a, &mut c);

    // Both must agree on the same value (LWW deterministic).
    let val_a = a
        .graph
        .get_node("s1")
        .unwrap()
        .properties
        .get("status")
        .unwrap()
        .clone();
    let val_c = c
        .graph
        .get_node("s1")
        .unwrap()
        .properties
        .get("status")
        .unwrap()
        .clone();
    assert_eq!(val_a, val_c, "LWW did not converge");
}

#[test]
fn partition_heal_add_wins_across_partition() {
    // A removes a node. C re-adds it. After heal, add-wins: node exists.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);
    let mut c = Peer::new("inst-c", &g);

    // Both start with the same node.
    a.add_node("s1", BTreeMap::new());
    a.sync_to(&mut c);

    // PARTITION: A removes, C re-adds.
    a.remove_node("s1");
    c.add_node(
        "s1",
        BTreeMap::from([("revived".into(), Value::Bool(true))]),
    );

    // HEAL.
    Peer::sync_bidi(&mut a, &mut c);

    // Add-wins: node should exist on both.
    assert!(
        a.graph.get_node("s1").is_some(),
        "add-wins failed on A after heal"
    );
    assert!(
        c.graph.get_node("s1").is_some(),
        "add-wins failed on C after heal"
    );
}

#[test]
fn partition_heal_edges_reconnect() {
    // During partition, A adds edges. C adds different edges.
    // After heal, all edges exist.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);
    let mut c = Peer::new("inst-c", &g);

    // Common topology.
    a.add_node("n1", BTreeMap::new());
    a.add_node("n2", BTreeMap::new());
    a.add_node("n3", BTreeMap::new());
    a.sync_to(&mut c);

    // PARTITION.
    a.add_edge("e-ab", "n1", "n2");
    c.add_edge("e-bc", "n2", "n3");

    // HEAL.
    Peer::sync_bidi(&mut a, &mut c);

    // Both should have both edges.
    let expected_edges: HashSet<String> = ["e-ab", "e-bc"].iter().map(|s| s.to_string()).collect();
    assert_eq!(a.live_edge_ids(), expected_edges, "A missing edges");
    assert_eq!(c.live_edge_ids(), expected_edges, "C missing edges");
}

// ===========================================================================
// Test: Snapshot Bootstrap → Delta Sync
// ===========================================================================

#[test]
fn snapshot_bootstrap_then_delta() {
    // A has history. B bootstraps from snapshot. A adds more. Delta sync works.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);

    a.add_node("n1", BTreeMap::new());
    a.add_node("n2", BTreeMap::new());
    a.add_edge("e1", "n1", "n2");

    // B bootstraps from A's snapshot.
    let snap = Snapshot::from_oplog(&a.oplog);
    let mut b = Peer::new("inst-b", &g);
    merge_entries(&mut b.oplog, &snap.entries[1..]).unwrap(); // skip genesis (already have it)
    b.rebuild_graph();

    assert_eq!(a.len(), b.len());
    assert_eq!(a.live_node_ids(), b.live_node_ids());

    // A adds more entries.
    a.add_node("n3", BTreeMap::new());
    a.add_edge("e2", "n2", "n3");

    // Delta sync (not snapshot again).
    a.sync_to(&mut b);

    assert_eq!(a.len(), b.len());
    assert!(b.graph.get_node("n3").is_some());
    assert!(b.graph.get_edge("e2").is_some());
}

// ===========================================================================
// Test: Ring Topology Convergence
// ===========================================================================

#[test]
fn ring_topology_convergence() {
    // 4 peers in a ring: A↔B↔C↔D↔A. Each writes. Ring sync converges.
    let g = genesis("inst-a");
    let mut a = Peer::new("inst-a", &g);
    let mut b = Peer::new("inst-b", &g);
    let mut c = Peer::new("inst-c", &g);
    let mut d = Peer::new("inst-d", &g);

    // Each peer writes a unique node.
    a.add_node("from-a", BTreeMap::new());
    b.add_node("from-b", BTreeMap::new());
    c.add_node("from-c", BTreeMap::new());
    d.add_node("from-d", BTreeMap::new());

    // Ring sync: A→B, B→C, C→D, D→A (one direction).
    a.sync_to(&mut b);
    b.sync_to(&mut c);
    c.sync_to(&mut d);
    d.sync_to(&mut a);

    // Second round to complete propagation.
    a.sync_to(&mut b);
    b.sync_to(&mut c);
    c.sync_to(&mut d);
    d.sync_to(&mut a);

    // All should have all 4 nodes.
    let expected: HashSet<String> = ["from-a", "from-b", "from-c", "from-d"]
        .iter()
        .map(|s| s.to_string())
        .collect();
    assert_eq!(a.live_node_ids(), expected, "A");
    assert_eq!(b.live_node_ids(), expected, "B");
    assert_eq!(c.live_node_ids(), expected, "C");
    assert_eq!(d.live_node_ids(), expected, "D");
}
