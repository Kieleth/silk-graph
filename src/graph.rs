use std::collections::{BTreeMap, HashMap, HashSet};

use crate::clock::LamportClock;
use crate::entry::{Entry, GraphOp, Value};
use crate::ontology::Ontology;

/// A materialized node in the graph.
#[derive(Debug, Clone, PartialEq)]
pub struct Node {
    pub node_id: String,
    pub node_type: String,
    pub subtype: Option<String>,
    pub label: String,
    pub properties: BTreeMap<String, Value>,
    /// Per-property Lamport clocks for LWW conflict resolution.
    /// Each property key tracks the clock of its last write, so
    /// concurrent updates to different properties don't conflict.
    pub property_clocks: HashMap<String, LamportClock>,
    /// Lamport clock of the entry that last modified this node.
    /// Used for add-wins semantics and label LWW.
    pub last_clock: LamportClock,
    /// Lamport clock of the most recent AddNode for this node.
    /// Used for add-wins semantics: remove only wins if its clock
    /// is strictly greater than last_add_clock.
    pub last_add_clock: LamportClock,
    /// Whether this node has been tombstoned (removed).
    pub tombstoned: bool,
}

/// A materialized edge in the graph.
#[derive(Debug, Clone, PartialEq)]
pub struct Edge {
    pub edge_id: String,
    pub edge_type: String,
    pub source_id: String,
    pub target_id: String,
    pub properties: BTreeMap<String, Value>,
    /// Per-property Lamport clocks for LWW conflict resolution.
    pub property_clocks: HashMap<String, LamportClock>,
    pub last_clock: LamportClock,
    /// Lamport clock of the most recent AddEdge for this edge.
    pub last_add_clock: LamportClock,
    pub tombstoned: bool,
}

/// Materialized graph — derived from the op log.
///
/// Provides fast queries without replaying the full log.
/// Updated incrementally as new entries arrive, or rebuilt
/// from scratch by replaying the entire op log.
///
/// CRDT semantics:
/// - **Add-wins** for topology (concurrent add + remove → node/edge exists)
/// - **LWW** (Last-Writer-Wins) per property key (highest Lamport clock wins)
/// - **Tombstones** for deletes (mark as deleted, don't physically remove)
pub struct MaterializedGraph {
    /// node_id → Node
    pub nodes: HashMap<String, Node>,
    /// edge_id → Edge
    pub edges: HashMap<String, Edge>,
    /// node_id → set of outgoing edge_ids
    pub outgoing: HashMap<String, HashSet<String>>,
    /// node_id → set of incoming edge_ids
    pub incoming: HashMap<String, HashSet<String>>,
    /// node_type → set of node_ids (type index)
    pub by_type: HashMap<String, HashSet<String>>,
    /// The ontology (for validation during materialization)
    pub ontology: Ontology,
}

impl MaterializedGraph {
    /// Create an empty materialized graph with the given ontology.
    pub fn new(ontology: Ontology) -> Self {
        Self {
            nodes: HashMap::new(),
            edges: HashMap::new(),
            outgoing: HashMap::new(),
            incoming: HashMap::new(),
            by_type: HashMap::new(),
            ontology,
        }
    }

    /// Apply a single entry to the graph (incremental materialization).
    pub fn apply(&mut self, entry: &Entry) {
        match &entry.payload {
            GraphOp::DefineOntology { .. } => {
                // Genesis — nothing to materialize.
            }
            GraphOp::AddNode {
                node_id,
                node_type,
                subtype,
                label,
                properties,
            } => {
                self.apply_add_node(
                    node_id,
                    node_type,
                    subtype.as_deref(),
                    label,
                    properties,
                    &entry.clock,
                );
            }
            GraphOp::AddEdge {
                edge_id,
                edge_type,
                source_id,
                target_id,
                properties,
            } => {
                self.apply_add_edge(
                    edge_id,
                    edge_type,
                    source_id,
                    target_id,
                    properties,
                    &entry.clock,
                );
            }
            GraphOp::UpdateProperty {
                entity_id,
                key,
                value,
            } => {
                self.apply_update_property(entity_id, key, value, &entry.clock);
            }
            GraphOp::RemoveNode { node_id } => {
                self.apply_remove_node(node_id, &entry.clock);
            }
            GraphOp::RemoveEdge { edge_id } => {
                self.apply_remove_edge(edge_id, &entry.clock);
            }
        }
    }

    /// Apply a sequence of entries (full rematerialization from op log).
    pub fn apply_all(&mut self, entries: &[&Entry]) {
        for entry in entries {
            self.apply(entry);
        }
    }

    /// Rebuild from scratch: clear everything and replay all entries.
    pub fn rebuild(&mut self, entries: &[&Entry]) {
        self.nodes.clear();
        self.edges.clear();
        self.outgoing.clear();
        self.incoming.clear();
        self.by_type.clear();
        self.apply_all(entries);
    }

    // -- Queries --

    /// Get a node by ID (returns None if not found or tombstoned).
    pub fn get_node(&self, node_id: &str) -> Option<&Node> {
        self.nodes.get(node_id).filter(|n| !n.tombstoned)
    }

    /// Get an edge by ID (returns None if not found or tombstoned).
    pub fn get_edge(&self, edge_id: &str) -> Option<&Edge> {
        self.edges.get(edge_id).filter(|e| !e.tombstoned)
    }

    /// Query all live nodes of a given type.
    pub fn nodes_by_type(&self, node_type: &str) -> Vec<&Node> {
        match self.by_type.get(node_type) {
            Some(ids) => ids
                .iter()
                .filter_map(|id| self.get_node(id))
                .collect(),
            None => vec![],
        }
    }

    /// Query all live nodes of a given subtype.
    pub fn nodes_by_subtype(&self, subtype: &str) -> Vec<&Node> {
        self.nodes
            .values()
            .filter(|n| !n.tombstoned && n.subtype.as_deref() == Some(subtype))
            .collect()
    }

    /// Query nodes by a property value.
    pub fn nodes_by_property(&self, key: &str, value: &Value) -> Vec<&Node> {
        self.nodes
            .values()
            .filter(|n| !n.tombstoned && n.properties.get(key) == Some(value))
            .collect()
    }

    /// Get outgoing edges for a node (only live edges with live endpoints).
    pub fn outgoing_edges(&self, node_id: &str) -> Vec<&Edge> {
        match self.outgoing.get(node_id) {
            Some(edge_ids) => edge_ids
                .iter()
                .filter_map(|eid| self.get_edge(eid))
                .filter(|e| self.is_node_live(&e.target_id))
                .collect(),
            None => vec![],
        }
    }

    /// Get incoming edges for a node (only live edges with live endpoints).
    pub fn incoming_edges(&self, node_id: &str) -> Vec<&Edge> {
        match self.incoming.get(node_id) {
            Some(edge_ids) => edge_ids
                .iter()
                .filter_map(|eid| self.get_edge(eid))
                .filter(|e| self.is_node_live(&e.source_id))
                .collect(),
            None => vec![],
        }
    }

    /// All live nodes.
    pub fn all_nodes(&self) -> Vec<&Node> {
        self.nodes.values().filter(|n| !n.tombstoned).collect()
    }

    /// All live edges (with live endpoints).
    pub fn all_edges(&self) -> Vec<&Edge> {
        self.edges
            .values()
            .filter(|e| {
                !e.tombstoned
                    && self.is_node_live(&e.source_id)
                    && self.is_node_live(&e.target_id)
            })
            .collect()
    }

    /// Neighbors of a node (connected via outgoing edges).
    pub fn neighbors(&self, node_id: &str) -> Vec<&str> {
        self.outgoing_edges(node_id)
            .iter()
            .map(|e| e.target_id.as_str())
            .collect()
    }

    /// Reverse neighbors (connected via incoming edges).
    pub fn reverse_neighbors(&self, node_id: &str) -> Vec<&str> {
        self.incoming_edges(node_id)
            .iter()
            .map(|e| e.source_id.as_str())
            .collect()
    }

    // -- CRDT application helpers --

    fn apply_add_node(
        &mut self,
        node_id: &str,
        node_type: &str,
        subtype: Option<&str>,
        label: &str,
        properties: &BTreeMap<String, Value>,
        clock: &LamportClock,
    ) {
        if let Some(existing) = self.nodes.get_mut(node_id) {
            // Add-wins: always resurrect from tombstone.
            existing.tombstoned = false;
            // Track the latest add clock for add-wins semantics.
            if clock_wins(clock, &existing.last_add_clock) {
                existing.last_add_clock = clock.clone();
            }
            // LWW merge for label, subtype, and properties.
            if clock_wins(clock, &existing.last_clock) {
                existing.label = label.to_string();
                existing.subtype = subtype.map(|s| s.to_string());
                existing.last_clock = clock.clone();
            }
            // Per-property LWW: each property from add_node competes
            // only with writes to the same key.
            for (k, v) in properties {
                let dominated = existing.property_clocks.get(k)
                    .map(|c| clock_wins(clock, c))
                    .unwrap_or(true);
                if dominated {
                    existing.properties.insert(k.clone(), v.clone());
                    existing.property_clocks.insert(k.clone(), clock.clone());
                }
            }
        } else {
            let property_clocks: HashMap<String, LamportClock> = properties.keys()
                .map(|k| (k.clone(), clock.clone()))
                .collect();
            let node = Node {
                node_id: node_id.to_string(),
                node_type: node_type.to_string(),
                subtype: subtype.map(|s| s.to_string()),
                label: label.to_string(),
                properties: properties.clone(),
                property_clocks,
                last_clock: clock.clone(),
                last_add_clock: clock.clone(),
                tombstoned: false,
            };
            self.by_type
                .entry(node_type.to_string())
                .or_default()
                .insert(node_id.to_string());
            self.nodes.insert(node_id.to_string(), node);
        }
    }

    fn apply_add_edge(
        &mut self,
        edge_id: &str,
        edge_type: &str,
        source_id: &str,
        target_id: &str,
        properties: &BTreeMap<String, Value>,
        clock: &LamportClock,
    ) {
        if let Some(existing) = self.edges.get_mut(edge_id) {
            // Add-wins: always resurrect if tombstoned.
            existing.tombstoned = false;
            if clock_wins(clock, &existing.last_add_clock) {
                existing.last_add_clock = clock.clone();
            }
            if clock_wins(clock, &existing.last_clock) {
                existing.last_clock = clock.clone();
            }
            // Per-property LWW for edge properties.
            for (k, v) in properties {
                let dominated = existing.property_clocks.get(k)
                    .map(|c| clock_wins(clock, c))
                    .unwrap_or(true);
                if dominated {
                    existing.properties.insert(k.clone(), v.clone());
                    existing.property_clocks.insert(k.clone(), clock.clone());
                }
            }
        } else {
            let property_clocks: HashMap<String, LamportClock> = properties.keys()
                .map(|k| (k.clone(), clock.clone()))
                .collect();
            let edge = Edge {
                edge_id: edge_id.to_string(),
                edge_type: edge_type.to_string(),
                source_id: source_id.to_string(),
                target_id: target_id.to_string(),
                properties: properties.clone(),
                property_clocks,
                last_clock: clock.clone(),
                last_add_clock: clock.clone(),
                tombstoned: false,
            };
            self.outgoing
                .entry(source_id.to_string())
                .or_default()
                .insert(edge_id.to_string());
            self.incoming
                .entry(target_id.to_string())
                .or_default()
                .insert(edge_id.to_string());
            self.edges.insert(edge_id.to_string(), edge);
        }
    }

    fn apply_update_property(
        &mut self,
        entity_id: &str,
        key: &str,
        value: &Value,
        clock: &LamportClock,
    ) {
        // Try node first, then edge. Per-property LWW: each key competes
        // only with other writes to the same key, not the entire entity.
        if let Some(node) = self.nodes.get_mut(entity_id) {
            let dominated = node.property_clocks.get(key)
                .map(|c| clock_wins(clock, c))
                .unwrap_or(true);
            if dominated {
                node.properties.insert(key.to_string(), value.clone());
                node.property_clocks.insert(key.to_string(), clock.clone());
            }
            // Update entity-level clock for add-wins tracking.
            if clock_wins(clock, &node.last_clock) {
                node.last_clock = clock.clone();
            }
        } else if let Some(edge) = self.edges.get_mut(entity_id) {
            let dominated = edge.property_clocks.get(key)
                .map(|c| clock_wins(clock, c))
                .unwrap_or(true);
            if dominated {
                edge.properties.insert(key.to_string(), value.clone());
                edge.property_clocks.insert(key.to_string(), clock.clone());
            }
            if clock_wins(clock, &edge.last_clock) {
                edge.last_clock = clock.clone();
            }
        }
        // If entity not found, silently ignore (may arrive out of order in sync).
    }

    fn apply_remove_node(&mut self, node_id: &str, clock: &LamportClock) {
        if let Some(node) = self.nodes.get_mut(node_id) {
            // Add-wins: only tombstone if the remove clock is strictly greater
            // than the last add clock. If a concurrent (or later) add exists,
            // the node stays alive.
            if clock_wins(clock, &node.last_add_clock) {
                node.tombstoned = true;
                node.last_clock = clock.clone();
            }
        }
        // Tombstoning a node doesn't physically remove edges — they just become
        // invisible via is_node_live() checks in queries.
    }

    fn apply_remove_edge(&mut self, edge_id: &str, clock: &LamportClock) {
        if let Some(edge) = self.edges.get_mut(edge_id) {
            // Add-wins: only tombstone if remove clock > last add clock.
            if clock_wins(clock, &edge.last_add_clock) {
                edge.tombstoned = true;
                edge.last_clock = clock.clone();
            }
        }
    }

    fn is_node_live(&self, node_id: &str) -> bool {
        self.nodes
            .get(node_id)
            .map(|n| !n.tombstoned)
            .unwrap_or(false)
    }
}

/// LWW comparison: returns true if `new_clock` wins over `existing_clock`.
/// Higher Lamport time wins. On tie, higher instance ID wins (deterministic).
fn clock_wins(new_clock: &LamportClock, existing_clock: &LamportClock) -> bool {
    new_clock.time > existing_clock.time
        || (new_clock.time == existing_clock.time && new_clock.id > existing_clock.id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::entry::Entry;
    use crate::ontology::{EdgeTypeDef, NodeTypeDef};

    fn test_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([
                ("entity".into(), NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                }),
                ("signal".into(), NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                }),
            ]),
            edge_types: BTreeMap::from([
                ("RUNS_ON".into(), EdgeTypeDef {
                    description: None,
                    source_types: vec!["entity".into()],
                    target_types: vec!["entity".into()],
                    properties: BTreeMap::new(),
                }),
                ("OBSERVES".into(), EdgeTypeDef {
                    description: None,
                    source_types: vec!["signal".into()],
                    target_types: vec!["entity".into()],
                    properties: BTreeMap::new(),
                }),
            ]),
        }
    }

    fn make_entry(op: GraphOp, clock_time: u64, author: &str) -> Entry {
        Entry::new(
            op,
            vec![],
            vec![],
            LamportClock { id: author.into(), time: clock_time },
            author,
        )
    }

    // -- test_graph.rs spec from docs/silk.md --

    #[test]
    fn add_node_appears_in_query() {
        let mut g = MaterializedGraph::new(test_ontology());
        let entry = make_entry(
            GraphOp::AddNode {
                node_id: "server-1".into(),
                node_type: "entity".into(),
                label: "Server 1".into(),
                properties: BTreeMap::from([("ip".into(), Value::String("10.0.0.1".into()))]),
                subtype: None,
            },
            1, "inst-a",
        );
        g.apply(&entry);

        let node = g.get_node("server-1").unwrap();
        assert_eq!(node.node_type, "entity");
        assert_eq!(node.label, "Server 1");
        assert_eq!(node.properties.get("ip"), Some(&Value::String("10.0.0.1".into())));
    }

    #[test]
    fn add_edge_creates_adjacency() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "svc".into(), node_type: "entity".into(), label: "svc".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "srv".into(), node_type: "entity".into(), label: "srv".into(), properties: BTreeMap::new(), subtype: None },
            2, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddEdge { edge_id: "e1".into(), edge_type: "RUNS_ON".into(), source_id: "svc".into(), target_id: "srv".into(), properties: BTreeMap::new() },
            3, "inst-a",
        ));

        // Both endpoints know about the edge.
        let out = g.outgoing_edges("svc");
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].target_id, "srv");

        let inc = g.incoming_edges("srv");
        assert_eq!(inc.len(), 1);
        assert_eq!(inc[0].source_id, "svc");

        assert_eq!(g.neighbors("svc"), vec!["srv"]);
    }

    #[test]
    fn update_property_reflected() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::UpdateProperty { entity_id: "s1".into(), key: "cpu".into(), value: Value::Float(85.5) },
            2, "inst-a",
        ));

        let node = g.get_node("s1").unwrap();
        assert_eq!(node.properties.get("cpu"), Some(&Value::Float(85.5)));
    }

    #[test]
    fn remove_node_cascades_edges() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "a".into(), node_type: "entity".into(), label: "a".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "b".into(), node_type: "entity".into(), label: "b".into(), properties: BTreeMap::new(), subtype: None },
            2, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddEdge { edge_id: "e1".into(), edge_type: "RUNS_ON".into(), source_id: "a".into(), target_id: "b".into(), properties: BTreeMap::new() },
            3, "inst-a",
        ));
        assert_eq!(g.all_edges().len(), 1);

        // Remove node 'b' — edge becomes invisible (dangling target).
        g.apply(&make_entry(
            GraphOp::RemoveNode { node_id: "b".into() },
            4, "inst-a",
        ));
        assert!(g.get_node("b").is_none());
        // Edge still exists but not returned by all_edges (target tombstoned).
        assert_eq!(g.all_edges().len(), 0);
        // Outgoing from 'a' also filters out dangling edges.
        assert_eq!(g.outgoing_edges("a").len(), 0);
    }

    #[test]
    fn remove_edge_preserves_nodes() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "a".into(), node_type: "entity".into(), label: "a".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "b".into(), node_type: "entity".into(), label: "b".into(), properties: BTreeMap::new(), subtype: None },
            2, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddEdge { edge_id: "e1".into(), edge_type: "RUNS_ON".into(), source_id: "a".into(), target_id: "b".into(), properties: BTreeMap::new() },
            3, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::RemoveEdge { edge_id: "e1".into() },
            4, "inst-a",
        ));

        // Nodes still exist.
        assert!(g.get_node("a").is_some());
        assert!(g.get_node("b").is_some());
        // Edge is gone.
        assert!(g.get_edge("e1").is_none());
        assert_eq!(g.all_edges().len(), 0);
    }

    #[test]
    fn query_by_type_filters() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s2".into(), node_type: "entity".into(), label: "s2".into(), properties: BTreeMap::new(), subtype: None },
            2, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "alert".into(), node_type: "signal".into(), label: "alert".into(), properties: BTreeMap::new(), subtype: None },
            3, "inst-a",
        ));

        let entities = g.nodes_by_type("entity");
        assert_eq!(entities.len(), 2);
        let signals = g.nodes_by_type("signal");
        assert_eq!(signals.len(), 1);
        assert_eq!(signals[0].node_id, "alert");
    }

    #[test]
    fn query_by_property_filters() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::from([("status".into(), Value::String("alive".into()))]), subtype: None },
            1, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s2".into(), node_type: "entity".into(), label: "s2".into(), properties: BTreeMap::from([("status".into(), Value::String("dead".into()))]), subtype: None },
            2, "inst-a",
        ));

        let alive = g.nodes_by_property("status", &Value::String("alive".into()));
        assert_eq!(alive.len(), 1);
        assert_eq!(alive[0].node_id, "s1");
    }

    #[test]
    fn materialization_from_empty() {
        // Build graph incrementally.
        let mut g1 = MaterializedGraph::new(test_ontology());
        let entries = vec![
            make_entry(GraphOp::DefineOntology { ontology: test_ontology() }, 0, "inst-a"),
            make_entry(GraphOp::AddNode { node_id: "a".into(), node_type: "entity".into(), label: "a".into(), properties: BTreeMap::new(), subtype: None }, 1, "inst-a"),
            make_entry(GraphOp::AddNode { node_id: "b".into(), node_type: "entity".into(), label: "b".into(), properties: BTreeMap::new(), subtype: None }, 2, "inst-a"),
            make_entry(GraphOp::AddEdge { edge_id: "e1".into(), edge_type: "RUNS_ON".into(), source_id: "a".into(), target_id: "b".into(), properties: BTreeMap::new() }, 3, "inst-a"),
        ];
        for e in &entries {
            g1.apply(e);
        }

        // Rebuild from scratch.
        let mut g2 = MaterializedGraph::new(test_ontology());
        let refs: Vec<&Entry> = entries.iter().collect();
        g2.rebuild(&refs);

        // Same result.
        assert_eq!(g1.all_nodes().len(), g2.all_nodes().len());
        assert_eq!(g1.all_edges().len(), g2.all_edges().len());
        for node in g1.all_nodes() {
            let n2 = g2.get_node(&node.node_id).unwrap();
            assert_eq!(node.node_type, n2.node_type);
            assert_eq!(node.properties, n2.properties);
        }
    }

    #[test]
    fn incremental_equals_full() {
        let entries = vec![
            make_entry(GraphOp::DefineOntology { ontology: test_ontology() }, 0, "inst-a"),
            make_entry(GraphOp::AddNode { node_id: "a".into(), node_type: "entity".into(), label: "a".into(), properties: BTreeMap::from([("x".into(), Value::Int(1))]), subtype: None }, 1, "inst-a"),
            make_entry(GraphOp::UpdateProperty { entity_id: "a".into(), key: "x".into(), value: Value::Int(2) }, 2, "inst-a"),
            make_entry(GraphOp::AddNode { node_id: "b".into(), node_type: "entity".into(), label: "b".into(), properties: BTreeMap::new(), subtype: None }, 3, "inst-a"),
            make_entry(GraphOp::AddEdge { edge_id: "e1".into(), edge_type: "RUNS_ON".into(), source_id: "a".into(), target_id: "b".into(), properties: BTreeMap::new() }, 4, "inst-a"),
            make_entry(GraphOp::RemoveEdge { edge_id: "e1".into() }, 5, "inst-a"),
        ];

        // Incremental.
        let mut g_inc = MaterializedGraph::new(test_ontology());
        for e in &entries {
            g_inc.apply(e);
        }

        // Full replay.
        let mut g_full = MaterializedGraph::new(test_ontology());
        let refs: Vec<&Entry> = entries.iter().collect();
        g_full.rebuild(&refs);

        // Property should be 2 (updated).
        assert_eq!(g_inc.get_node("a").unwrap().properties.get("x"), Some(&Value::Int(2)));
        assert_eq!(g_full.get_node("a").unwrap().properties.get("x"), Some(&Value::Int(2)));
        // Edge should be removed.
        assert_eq!(g_inc.all_edges().len(), 0);
        assert_eq!(g_full.all_edges().len(), 0);
    }

    #[test]
    fn lww_concurrent_property_update() {
        // Two instances update the same property — higher clock wins.
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        // inst-a sets status=alive at time 2
        g.apply(&make_entry(
            GraphOp::UpdateProperty { entity_id: "s1".into(), key: "status".into(), value: Value::String("alive".into()) },
            2, "inst-a",
        ));
        // inst-b sets status=dead at time 3 — wins (higher clock)
        g.apply(&make_entry(
            GraphOp::UpdateProperty { entity_id: "s1".into(), key: "status".into(), value: Value::String("dead".into()) },
            3, "inst-b",
        ));
        assert_eq!(
            g.get_node("s1").unwrap().properties.get("status"),
            Some(&Value::String("dead".into()))
        );
    }

    #[test]
    fn lww_tiebreak_by_instance_id() {
        // Same clock time — higher instance ID wins.
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        // Both at time 5, inst-b > inst-a lexicographically.
        g.apply(&make_entry(
            GraphOp::UpdateProperty { entity_id: "s1".into(), key: "x".into(), value: Value::Int(1) },
            5, "inst-a",
        ));
        g.apply(&make_entry(
            GraphOp::UpdateProperty { entity_id: "s1".into(), key: "x".into(), value: Value::Int(2) },
            5, "inst-b",
        ));
        assert_eq!(g.get_node("s1").unwrap().properties.get("x"), Some(&Value::Int(2)));
    }

    #[test]
    fn lww_per_property_concurrent_different_keys() {
        // Two instances concurrently update DIFFERENT properties at the same
        // clock time. Both updates must be accepted — they don't conflict.
        // This requires per-property LWW, not node-level LWW.
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode {
                node_id: "s1".into(),
                node_type: "entity".into(),
                label: "s1".into(),
                properties: BTreeMap::from([
                    ("x".into(), Value::Int(0)),
                    ("y".into(), Value::Int(0)),
                ]),
                subtype: None,
            },
            1, "inst-a",
        ));
        // inst-a updates "x" at time 3
        g.apply(&make_entry(
            GraphOp::UpdateProperty {
                entity_id: "s1".into(),
                key: "x".into(),
                value: Value::Int(42),
            },
            3, "inst-a",
        ));
        // inst-b updates "y" at time 3 (concurrent, different property)
        g.apply(&make_entry(
            GraphOp::UpdateProperty {
                entity_id: "s1".into(),
                key: "y".into(),
                value: Value::Int(99),
            },
            3, "inst-b",
        ));

        let node = g.get_node("s1").unwrap();
        // Both updates must be applied — no conflict.
        assert_eq!(node.properties.get("x"), Some(&Value::Int(42)),
            "update to 'x' must not be rejected by concurrent update to 'y'");
        assert_eq!(node.properties.get("y"), Some(&Value::Int(99)),
            "update to 'y' must not be rejected by concurrent update to 'x'");
    }

    #[test]
    fn lww_per_property_order_independent() {
        // Same scenario but applied in reverse order — result must be identical.
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode {
                node_id: "s1".into(),
                node_type: "entity".into(),
                label: "s1".into(),
                properties: BTreeMap::from([
                    ("x".into(), Value::Int(0)),
                    ("y".into(), Value::Int(0)),
                ]),
                subtype: None,
            },
            1, "inst-a",
        ));
        // Apply inst-b first this time
        g.apply(&make_entry(
            GraphOp::UpdateProperty {
                entity_id: "s1".into(),
                key: "y".into(),
                value: Value::Int(99),
            },
            3, "inst-b",
        ));
        g.apply(&make_entry(
            GraphOp::UpdateProperty {
                entity_id: "s1".into(),
                key: "x".into(),
                value: Value::Int(42),
            },
            3, "inst-a",
        ));

        let node = g.get_node("s1").unwrap();
        assert_eq!(node.properties.get("x"), Some(&Value::Int(42)));
        assert_eq!(node.properties.get("y"), Some(&Value::Int(99)));
    }

    #[test]
    fn add_wins_over_remove() {
        // Concurrent add + remove → node should exist (add-wins).
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1".into(), properties: BTreeMap::new(), subtype: None },
            1, "inst-a",
        ));
        // Remove at time 2.
        g.apply(&make_entry(
            GraphOp::RemoveNode { node_id: "s1".into() },
            2, "inst-a",
        ));
        assert!(g.get_node("s1").is_none());

        // Re-add at time 3 (add-wins — resurrects).
        g.apply(&make_entry(
            GraphOp::AddNode { node_id: "s1".into(), node_type: "entity".into(), label: "s1 v2".into(), properties: BTreeMap::new(), subtype: None },
            3, "inst-b",
        ));
        let node = g.get_node("s1").unwrap();
        assert_eq!(node.label, "s1 v2");
        assert!(!node.tombstoned);
    }
}
