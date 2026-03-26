//! Level 4 stress tests — many peers, chaos, convergence under load.
//!
//! These test the sync protocol at scale: mesh topologies, chain topologies,
//! random operations with random sync ordering, and convergence guarantees.

use std::collections::{BTreeMap, HashSet};

use silk::clock::LamportClock;
use silk::entry::{Entry, GraphOp, Hash};
use silk::graph::MaterializedGraph;
use silk::ontology::{NodeTypeDef, Ontology};
use silk::oplog::OpLog;
use silk::sync::{entries_missing, merge_entries, SyncOffer};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
        LamportClock::new("seed"),
        "seed",
    )
}

struct Peer {
    id: String,
    oplog: OpLog,
    graph: MaterializedGraph,
    clock: LamportClock,
}

impl Peer {
    fn new(id: &str, g: &Entry) -> Self {
        let mut graph = MaterializedGraph::new(test_ontology());
        graph.apply(g);
        Self {
            id: id.to_string(),
            oplog: OpLog::new(g.clone()),
            graph,
            clock: LamportClock::new(id),
        }
    }

    fn add_node(&mut self, node_id: &str) {
        self.clock.tick();
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: node_id.into(),
                node_type: "entity".into(),
                label: node_id.into(),
                properties: BTreeMap::new(),
                subtype: None,
            },
            self.oplog.heads(),
            vec![],
            self.clock.clone(),
            &self.id,
        );
        self.graph.apply(&entry);
        self.oplog.append(entry).unwrap();
    }

    fn sync_push_to(src: &Peer, dst: &mut Peer) {
        let offer = SyncOffer::from_oplog(&dst.oplog, dst.clock.physical_ms, dst.clock.logical);
        let payload = entries_missing(&src.oplog, &offer);
        if !payload.entries.is_empty() {
            let merged = merge_entries(&mut dst.oplog, &payload.entries).unwrap();
            if merged > 0 {
                let all = dst.oplog.entries_since(None);
                let refs: Vec<&Entry> = all.iter().copied().collect();
                dst.graph.rebuild(&refs);
                for entry in &payload.entries {
                    dst.clock.merge(&entry.clock);
                }
            }
        }
    }

    fn live_node_ids(&self) -> HashSet<String> {
        self.graph
            .all_nodes()
            .iter()
            .map(|n| n.node_id.clone())
            .collect()
    }
}

/// Sync from peers[src] to peers[dst] with correct split_at_mut borrowing.
fn sync_pair(peers: &mut [Peer], src: usize, dst: usize) {
    assert_ne!(src, dst);
    if src < dst {
        let (left, right) = peers.split_at_mut(dst);
        Peer::sync_push_to(&left[src], &mut right[0]);
    } else {
        let (left, right) = peers.split_at_mut(src);
        Peer::sync_push_to(&right[0], &mut left[dst]);
    }
}

// ===========================================================================
// Test: 5-Peer Full Mesh
// ===========================================================================

#[test]
fn five_peer_mesh_convergence() {
    // 5 peers, each writes 10 unique nodes. Full mesh sync. All converge.
    let g = genesis();
    let mut peers: Vec<Peer> = (0..5).map(|i| Peer::new(&format!("p{i}"), &g)).collect();

    // Each peer writes 10 nodes.
    for i in 0..5 {
        for j in 0..10 {
            peers[i].add_node(&format!("p{i}-n{j}"));
        }
    }

    // Full mesh sync: every pair syncs bidirectionally.
    // Need multiple rounds because a single pass may not propagate everything.
    for _round in 0..3 {
        for i in 0..5 {
            for j in 0..5 {
                if i != j {
                    sync_pair(&mut peers, i, j);
                }
            }
        }
    }

    // All 5 peers should have all 50 nodes.
    let expected: HashSet<String> = (0..5)
        .flat_map(|i| (0..10).map(move |j| format!("p{i}-n{j}")))
        .collect();
    assert_eq!(expected.len(), 50);

    for (i, peer) in peers.iter().enumerate() {
        let nodes = peer.live_node_ids();
        assert_eq!(
            nodes,
            expected,
            "Peer p{i} has {} nodes, expected 50",
            nodes.len()
        );
    }
}

// ===========================================================================
// Test: 10-Peer Chain
// ===========================================================================

#[test]
fn ten_peer_chain_convergence() {
    // 10 peers in a chain: P0↔P1↔P2↔...↔P9.
    // Each writes 5 nodes. Chain sync propagates end-to-end.
    let g = genesis();
    let mut peers: Vec<Peer> = (0..10).map(|i| Peer::new(&format!("p{i}"), &g)).collect();

    // Each peer writes 5 nodes.
    for i in 0..10 {
        for j in 0..5 {
            peers[i].add_node(&format!("p{i}-n{j}"));
        }
    }

    // Chain sync: propagate left-to-right, then right-to-left.
    // Repeat enough rounds for full propagation (chain length = 10).
    for _round in 0..10 {
        // Left to right.
        for i in 0..9 {
            let (left, right) = peers.split_at_mut(i + 1);
            Peer::sync_push_to(&left[i], &mut right[0]);
        }
        // Right to left.
        for i in (1..10).rev() {
            let (left, right) = peers.split_at_mut(i);
            Peer::sync_push_to(&right[0], &mut left[i - 1]);
        }
    }

    // All 10 peers should have all 50 nodes.
    let expected: HashSet<String> = (0..10)
        .flat_map(|i| (0..5).map(move |j| format!("p{i}-n{j}")))
        .collect();
    assert_eq!(expected.len(), 50);

    for (i, peer) in peers.iter().enumerate() {
        let nodes = peer.live_node_ids();
        assert_eq!(
            nodes,
            expected,
            "Peer p{i} has {} nodes, expected 50",
            nodes.len()
        );
    }
}

// ===========================================================================
// Test: Chaos — Random Ops + Random Sync → Convergence
// ===========================================================================

#[test]
fn chaos_random_ops_random_sync() {
    // 4 peers. 200 random operations, interleaved with random syncs.
    // After full sync at the end, all peers must converge.
    //
    // Uses a deterministic PRNG (simple LCG) for reproducibility.
    let g = genesis();
    let mut peers: Vec<Peer> = (0..4).map(|i| Peer::new(&format!("p{i}"), &g)).collect();

    // Simple deterministic PRNG (LCG).
    let mut rng_state: u64 = 42;
    let mut next_rng = || -> u64 {
        rng_state = rng_state.wrapping_mul(6364136223846793005).wrapping_add(1);
        rng_state >> 33
    };

    // Phase 1: 200 random writes + occasional syncs.
    for op_idx in 0..200 {
        let peer_idx = (next_rng() % 4) as usize;
        let node_id = format!("chaos-{op_idx}");
        peers[peer_idx].add_node(&node_id);

        // Every 10 ops, do a random sync between two peers.
        if op_idx % 10 == 9 {
            let src = (next_rng() % 4) as usize;
            let mut dst = (next_rng() % 4) as usize;
            if dst == src {
                dst = (src + 1) % 4;
            }
            sync_pair(&mut peers, src, dst);
        }
    }

    // Phase 2: Full mesh sync to converge.
    for _round in 0..4 {
        for i in 0..4 {
            for j in 0..4 {
                if i != j {
                    sync_pair(&mut peers, i, j);
                }
            }
        }
    }

    // Phase 3: Verify convergence.
    let expected: HashSet<String> = (0..200).map(|i| format!("chaos-{i}")).collect();
    assert_eq!(expected.len(), 200);

    for (i, peer) in peers.iter().enumerate() {
        let nodes = peer.live_node_ids();
        assert_eq!(
            nodes,
            expected,
            "Peer p{i} has {} nodes, expected 200",
            nodes.len()
        );
    }

    // All peers should have the same entry count and heads.
    let len_0 = peers[0].oplog.len();
    let heads_0: HashSet<Hash> = peers[0].oplog.heads().into_iter().collect();
    for (i, peer) in peers.iter().enumerate().skip(1) {
        assert_eq!(peer.oplog.len(), len_0, "Peer p{i} entry count diverges");
        let heads_i: HashSet<Hash> = peer.oplog.heads().into_iter().collect();
        assert_eq!(heads_i, heads_0, "Peer p{i} heads diverge");
    }
}
