mod conversions;
mod obslog;
mod snapshot;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;

use crate::clock::LamportClock;
use crate::engine;
use crate::entry::{Entry, GraphOp, Hash, Value};
use crate::graph::MaterializedGraph;
use crate::ontology::Ontology;
use crate::oplog::OpLog;
use crate::store::Store;

use conversions::{
    convert_props, edge_to_pydict, entry_to_pydict, extract_ontology_from_genesis, node_to_pydict,
    ontology_arg_to_json, parse_hex_hash, py_to_value, value_to_py,
};

// ---------------------------------------------------------------------------
// PyGraphStore — ontology-first graph store (in-memory or persistent)
// ---------------------------------------------------------------------------

/// Graph store with ontology validation. Supports two modes:
/// - In-memory (default): data lives only in memory.
/// - Persistent: backed by redb on disk via `Store`.
///
/// The Python API is identical in both modes.
#[pyclass]
pub struct PyGraphStore {
    /// In-memory mode uses OpLog directly; persistent mode uses Store (which wraps OpLog).
    backend: Backend,
    /// Materialized graph — updated incrementally on each append.
    graph: MaterializedGraph,
    /// node_id → node_type, used for edge source/target validation.
    /// Redundant with graph.nodes — kept in sync manually during append/merge/rebuild.
    /// Only tracks live (non-tombstoned) nodes. Compaction excludes tombstoned nodes,
    /// so this map stays consistent as long as add/remove operations update both
    /// graph.nodes and node_types in the same code path.
    node_types: HashMap<String, String>,
    instance_id: String,
    clock: LamportClock,
    ontology: Ontology,
    /// Registered subscribers: (sub_id, callback). See D-023.
    subscribers: Vec<(u64, PyObject)>,
    /// Monotonic counter for subscriber IDs.
    next_sub_id: u64,
    /// D-027: ed25519 signing key for auto-signing entries.
    #[cfg(feature = "signing")]
    signing_key: Option<ed25519_dalek::SigningKey>,
    /// D-027: trusted author public keys (author_id → verifying key).
    #[cfg(feature = "signing")]
    key_registry: HashMap<String, ed25519_dalek::VerifyingKey>,
    /// D-027: reject unsigned entries on merge when true.
    #[cfg(feature = "signing")]
    require_signatures: bool,
    /// R-05: Gossip peer registry for logarithmic sync target selection.
    gossip: crate::gossip::PeerRegistry,
}

enum Backend {
    Memory(OpLog),
    Persistent(Store),
}

impl Backend {
    fn oplog(&self) -> &OpLog {
        match self {
            Backend::Memory(oplog) => oplog,
            Backend::Persistent(store) => &store.oplog,
        }
    }

    fn append(&mut self, entry: Entry) -> Result<bool, String> {
        match self {
            Backend::Memory(oplog) => oplog.append(entry).map_err(|e| e.to_string()),
            Backend::Persistent(store) => store.append(entry).map_err(|e| e.to_string()),
        }
    }
}

#[pymethods]
impl PyGraphStore {
    /// Create a new graph store with the given ontology.
    ///
    /// - `instance_id`: unique identifier for this instance.
    /// - `ontology`: JSON string OR Python dict defining the graph ontology.
    /// - `path` (optional): file path for persistent storage (redb).
    ///   If omitted, the store is purely in-memory.
    #[new]
    #[pyo3(signature = (instance_id, ontology, path=None))]
    fn new(
        instance_id: String,
        ontology: &pyo3::Bound<'_, pyo3::PyAny>,
        path: Option<String>,
    ) -> PyResult<Self> {
        let ontology_json = ontology_arg_to_json(ontology)?;
        let mut ontology: Ontology = serde_json::from_str(&ontology_json).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid ontology JSON: {e}"))
        })?;

        ontology.validate_self().map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("ontology validation failed: {e}"))
        })?;

        let mut clock = LamportClock::new(&instance_id);
        clock.tick();

        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: ontology.clone(),
            },
            vec![],
            vec![],
            clock.clone(),
            &instance_id,
        );

        let mut graph = MaterializedGraph::new(ontology.clone());
        let mut node_types = HashMap::new();

        let backend = match path {
            Some(p) => {
                let store = Store::open(&PathBuf::from(p), Some(genesis))
                    .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
                // Rebuild materialized graph from existing entries (handles reopen).
                let all = store.oplog.entries_since(None);
                let refs: Vec<&Entry> = all.iter().copied().collect();
                graph.rebuild(&refs);
                for entry in &all {
                    match &entry.payload {
                        GraphOp::AddNode {
                            node_id, node_type, ..
                        } => {
                            node_types.insert(node_id.clone(), node_type.clone());
                        }
                        GraphOp::RemoveNode { node_id } => {
                            node_types.remove(node_id);
                        }
                        GraphOp::Checkpoint { ops, .. } => {
                            for op in ops {
                                if let GraphOp::AddNode {
                                    node_id, node_type, ..
                                } = op
                                {
                                    node_types.insert(node_id.clone(), node_type.clone());
                                }
                            }
                        }
                        _ => {}
                    }
                }
                // Advance clock past any existing entries.
                for entry in &all {
                    clock.merge(&entry.clock);
                }
                // R-03: sync ontology from graph (may have been extended).
                ontology = graph.ontology.clone();
                Backend::Persistent(store)
            }
            None => {
                let oplog = OpLog::new(genesis);
                Backend::Memory(oplog)
            }
        };

        Ok(Self {
            backend,
            graph,
            node_types,
            instance_id: instance_id.clone(),
            clock,
            ontology,
            subscribers: Vec::new(),
            next_sub_id: 0,
            #[cfg(feature = "signing")]
            signing_key: None,
            #[cfg(feature = "signing")]
            key_registry: HashMap::new(),
            #[cfg(feature = "signing")]
            require_signatures: false,
            gossip: crate::gossip::PeerRegistry::with_instance_id(&instance_id),
        })
    }

    /// Open an existing persistent store (no genesis needed).
    #[staticmethod]
    fn open(path: String) -> PyResult<Self> {
        let store = Store::open(&PathBuf::from(&path), None)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;

        // Extract ontology from genesis entry (DefineOntology or Checkpoint).
        let oplog = &store.oplog;
        let all = oplog.entries_since(None);
        let genesis = all
            .first()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("store has no entries"))?;
        let ontology = extract_ontology_from_genesis(genesis)?;

        // Reconstruct node_types from replaying ops.
        let mut node_types = HashMap::new();
        for entry in &all {
            match &entry.payload {
                GraphOp::AddNode {
                    node_id, node_type, ..
                } => {
                    node_types.insert(node_id.clone(), node_type.clone());
                }
                GraphOp::RemoveNode { node_id } => {
                    node_types.remove(node_id);
                }
                GraphOp::Checkpoint { ops, .. } => {
                    for op in ops {
                        if let GraphOp::AddNode {
                            node_id, node_type, ..
                        } = op
                        {
                            node_types.insert(node_id.clone(), node_type.clone());
                        }
                    }
                }
                _ => {}
            }
        }

        let instance_id = genesis.author.clone();
        let clock = genesis.clock.clone();

        // Materialize graph from op log.
        let mut graph = MaterializedGraph::new(ontology.clone());
        let refs: Vec<&Entry> = all.iter().copied().collect();
        graph.rebuild(&refs);
        // R-03: sync ontology from graph (may have been extended).
        let ontology = graph.ontology.clone();

        let gossip = crate::gossip::PeerRegistry::with_instance_id(&instance_id);
        Ok(Self {
            backend: Backend::Persistent(store),
            graph,
            node_types,
            instance_id,
            clock,
            ontology,
            subscribers: Vec::new(),
            next_sub_id: 0,
            #[cfg(feature = "signing")]
            signing_key: None,
            #[cfg(feature = "signing")]
            key_registry: HashMap::new(),
            #[cfg(feature = "signing")]
            require_signatures: false,
            gossip,
        })
    }

    /// Append an AddNode operation. Returns the hex hash of the new entry.
    #[pyo3(signature = (node_id, node_type, label, properties=None, subtype=None))]
    fn add_node(
        &mut self,
        node_id: String,
        node_type: String,
        label: String,
        properties: Option<&Bound<'_, PyDict>>,
        subtype: Option<String>,
    ) -> PyResult<String> {
        let props = convert_props(properties)?;

        self.ontology
            .validate_node(&node_type, subtype.as_deref(), &props)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let op = GraphOp::AddNode {
            node_id: node_id.clone(),
            node_type: node_type.clone(),
            subtype,
            label,
            properties: props,
        };
        let hex = self.append(op)?;
        self.node_types.insert(node_id, node_type);
        Ok(hex)
    }

    /// Append an AddEdge operation. Returns the hex hash of the new entry.
    #[pyo3(signature = (edge_id, edge_type, source_id, target_id, properties=None))]
    fn add_edge(
        &mut self,
        edge_id: String,
        edge_type: String,
        source_id: String,
        target_id: String,
        properties: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<String> {
        let props = convert_props(properties)?;

        let source_type = self.node_types.get(&source_id).ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "source node '{source_id}' not found — add it before creating edges"
            ))
        })?;
        let target_type = self.node_types.get(&target_id).ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "target node '{target_id}' not found — add it before creating edges"
            ))
        })?;

        self.ontology
            .validate_edge(&edge_type, source_type, target_type, &props)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let op = GraphOp::AddEdge {
            edge_id,
            edge_type,
            source_id,
            target_id,
            properties: props,
        };
        self.append(op)
    }

    /// Append an UpdateProperty operation. Returns the hex hash.
    fn update_property(
        &mut self,
        entity_id: String,
        key: String,
        value: &Bound<'_, pyo3::PyAny>,
    ) -> PyResult<String> {
        let val = py_to_value(value)?;
        let op = GraphOp::UpdateProperty {
            entity_id,
            key,
            value: val,
        };
        self.append(op)
    }

    /// Append a RemoveNode operation. Returns the hex hash.
    fn remove_node(&mut self, node_id: String) -> PyResult<String> {
        self.node_types.remove(&node_id);
        self.append(GraphOp::RemoveNode { node_id })
    }

    /// Append a RemoveEdge operation. Returns the hex hash.
    fn remove_edge(&mut self, edge_id: String) -> PyResult<String> {
        self.append(GraphOp::RemoveEdge { edge_id })
    }

    /// R-03: Extend the ontology with new types/properties.
    /// Takes a JSON string or Python dict matching OntologyExtension format.
    /// Only additive changes allowed (monotonic).
    fn extend_ontology(&mut self, extension: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<String> {
        let json_str = ontology_arg_to_json(extension)?;
        let extension: crate::ontology::OntologyExtension = serde_json::from_str(&json_str)
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("invalid extension JSON: {e}"))
            })?;

        // Validate monotonicity against current ontology
        let mut test_ontology = self.ontology.clone();
        test_ontology
            .merge_extension(&extension)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let op = GraphOp::ExtendOntology {
            extension: extension.clone(),
        };
        let hex = self.append(op)?;
        // Update local ontology to stay in sync with graph.ontology
        self.ontology
            .merge_extension(&extension)
            .expect("already validated");
        Ok(hex)
    }

    /// Get an entry by hex hash. Returns None if not found.
    fn get(&self, hex_hash: &str) -> PyResult<Option<PyObject>> {
        let hash = parse_hex_hash(hex_hash)?;
        Ok(self
            .backend
            .oplog()
            .get(&hash)
            .map(|e| Python::with_gil(|py| entry_to_pydict(py, e).unwrap().into())))
    }

    /// Return current DAG head hashes as list of hex strings.
    fn heads(&self) -> Vec<String> {
        self.backend
            .oplog()
            .heads()
            .iter()
            .map(|h| hex::encode(h))
            .collect()
    }

    /// Total number of entries in the store (including genesis).
    fn len(&self) -> usize {
        self.backend.oplog().len()
    }

    /// Verify structural integrity of the oplog (INV-6).
    /// Returns (ok, errors) where ok is True if no violations found.
    fn verify_integrity(&self) -> (bool, Vec<String>) {
        let errors = self.backend.oplog().verify_integrity();
        (errors.is_empty(), errors)
    }

    /// Estimated memory usage in bytes (Rust heap).
    /// Returns dict with oplog_bytes, graph_bytes, total_bytes.
    fn memory_usage(&self) -> std::collections::HashMap<String, usize> {
        let oplog_bytes = self.backend.oplog().estimated_memory_bytes();
        let graph_bytes = self.graph.estimated_memory_bytes();
        let mut result = std::collections::HashMap::new();
        result.insert("oplog_bytes".to_string(), oplog_bytes);
        result.insert("graph_bytes".to_string(), graph_bytes);
        result.insert("total_bytes".to_string(), oplog_bytes + graph_bytes);
        result
    }

    /// Instance identifier.
    fn instance_id(&self) -> &str {
        &self.instance_id
    }

    /// Current clock time as (physical_ms, logical) tuple.
    fn clock_time(&self) -> (u64, u32) {
        self.clock.as_tuple()
    }

    /// Return the ontology as a JSON string.
    fn ontology_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.ontology)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Return the list of valid node types.
    fn node_type_names(&self) -> Vec<String> {
        self.ontology.node_types.keys().cloned().collect()
    }

    /// Return the list of valid edge types.
    fn edge_type_names(&self) -> Vec<String> {
        self.ontology.edge_types.keys().cloned().collect()
    }

    /// Return all entries since a given hash (delta for sync).
    /// If hash is None, returns all entries.
    #[pyo3(signature = (hex_hash=None))]
    fn entries_since(&self, hex_hash: Option<&str>) -> PyResult<Vec<PyObject>> {
        let hash = match hex_hash {
            Some(h) => Some(parse_hex_hash(h)?),
            None => None,
        };
        let entries = self.backend.oplog().entries_since(hash.as_ref());
        Python::with_gil(|py| entries.iter().map(|e| entry_to_pydict(py, e)).collect())
    }

    // -- Graph queries --

    /// Get a node by ID. Returns dict or None.
    fn get_node(&self, py: Python<'_>, node_id: &str) -> PyResult<Option<PyObject>> {
        Ok(self
            .graph
            .get_node(node_id)
            .map(|n| node_to_pydict(py, n).unwrap()))
    }

    /// Get an edge by ID. Returns dict or None.
    fn get_edge(&self, py: Python<'_>, edge_id: &str) -> PyResult<Option<PyObject>> {
        Ok(self
            .graph
            .get_edge(edge_id)
            .map(|e| edge_to_pydict(py, e).unwrap()))
    }

    /// Query nodes by type. Returns list of node dicts.
    fn query_nodes_by_type(&self, py: Python<'_>, node_type: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .nodes_by_type(node_type)
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    /// Query nodes by subtype. Returns list of node dicts.
    fn query_nodes_by_subtype(&self, py: Python<'_>, subtype: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .nodes_by_subtype(subtype)
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    /// Query nodes by property value. Returns list of node dicts.
    fn query_nodes_by_property(
        &self,
        py: Python<'_>,
        key: &str,
        value: &Bound<'_, pyo3::PyAny>,
    ) -> PyResult<Vec<PyObject>> {
        let val = py_to_value(value)?;
        self.graph
            .nodes_by_property(key, &val)
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    /// All live nodes. Returns list of node dicts.
    fn all_nodes(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.graph
            .all_nodes()
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    /// All live edges. Returns list of edge dicts.
    fn all_edges(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.graph
            .all_edges()
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    /// Outgoing edges from a node. Returns list of edge dicts.
    fn outgoing_edges(&self, py: Python<'_>, node_id: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .outgoing_edges(node_id)
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    /// Incoming edges to a node. Returns list of edge dicts.
    fn incoming_edges(&self, py: Python<'_>, node_id: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .incoming_edges(node_id)
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    /// Neighbor node IDs (via outgoing edges).
    fn neighbors(&self, node_id: &str) -> Vec<String> {
        self.graph
            .neighbors(node_id)
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    // -- Engine methods --

    /// BFS traversal from a start node. Returns list of node IDs.
    #[pyo3(signature = (start, max_depth=None, edge_type=None))]
    fn bfs(&self, start: &str, max_depth: Option<usize>, edge_type: Option<&str>) -> Vec<String> {
        engine::bfs(&self.graph, start, max_depth, edge_type)
    }

    /// DFS traversal from a start node. Returns list of node IDs.
    #[pyo3(signature = (start, max_depth=None, edge_type=None))]
    fn dfs(&self, start: &str, max_depth: Option<usize>, edge_type: Option<&str>) -> Vec<String> {
        engine::dfs(&self.graph, start, max_depth, edge_type)
    }

    /// Shortest path between two nodes. Returns list of node IDs or None.
    fn shortest_path(&self, start: &str, end: &str) -> Option<Vec<String>> {
        engine::shortest_path(&self.graph, start, end)
    }

    /// Impact analysis: what depends on this node? Returns list of node IDs.
    #[pyo3(signature = (node_id, max_depth=None))]
    fn impact_analysis(&self, node_id: &str, max_depth: Option<usize>) -> Vec<String> {
        engine::impact_analysis(&self.graph, node_id, max_depth)
    }

    /// Subgraph extraction: nodes and edges within N hops.
    /// Returns dict with "nodes" and "edges" keys.
    fn subgraph(&self, py: Python<'_>, start: &str, hops: usize) -> PyResult<PyObject> {
        let (nodes, edges) = engine::subgraph(&self.graph, start, hops);
        let dict = PyDict::new(py);
        dict.set_item("nodes", nodes)?;
        dict.set_item("edges", edges)?;
        Ok(dict.into())
    }

    /// Pattern match: find chains matching a sequence of node types.
    /// Returns list of chains (each chain is a list of node IDs).
    /// Limited to 1000 results by default to prevent runaway expansion on dense graphs.
    #[pyo3(signature = (type_sequence, max_results=1000))]
    fn pattern_match(
        &self,
        py: Python<'_>,
        type_sequence: Vec<String>,
        max_results: usize,
    ) -> PyResult<PyObject> {
        let refs: Vec<&str> = type_sequence.iter().map(|s| s.as_str()).collect();
        let chains = engine::pattern_match(&self.graph, &refs, max_results);
        let list = PyList::empty(py);
        for chain in chains {
            let py_chain = PyList::new(py, &chain)?;
            list.append(py_chain)?;
        }
        Ok(list.into())
    }

    /// Topological sort. Returns list of node IDs or None if cycle detected.
    fn topological_sort(&self) -> Option<Vec<String>> {
        engine::topological_sort(&self.graph)
    }

    /// Cycle detection. Returns true if graph has a cycle.
    fn has_cycle(&self) -> bool {
        engine::has_cycle(&self.graph)
    }

    // -- Sync methods --

    /// Generate a sync offer (heads + bloom filter) as bytes.
    ///
    /// Send this to a peer so they can compute which entries you're missing.
    fn generate_sync_offer(&self) -> PyResult<Vec<u8>> {
        let offer = crate::sync::SyncOffer::from_oplog(
            self.backend.oplog(),
            self.clock.physical_ms,
            self.clock.logical,
        );
        Ok(offer.to_bytes())
    }

    /// Receive a remote peer's sync offer and compute the payload to send back.
    ///
    /// Takes the remote offer as bytes, returns a sync payload (entries + need list) as bytes.
    fn receive_sync_offer(&self, offer_bytes: Vec<u8>) -> PyResult<Vec<u8>> {
        let offer = crate::sync::SyncOffer::from_bytes(&offer_bytes).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid sync offer: {e}"))
        })?;
        let payload = crate::sync::entries_missing(self.backend.oplog(), &offer);
        Ok(payload.to_bytes())
    }

    /// Generate a filtered sync payload — only entries matching the given node types,
    /// plus all causal ancestors (ensures causal completeness).
    /// This reduces bandwidth for peers that only need a subset of the graph.
    #[pyo3(signature = (offer_bytes, node_types))]
    fn receive_filtered_sync_offer(
        &self,
        offer_bytes: Vec<u8>,
        node_types: Vec<String>,
    ) -> PyResult<Vec<u8>> {
        let offer = crate::sync::SyncOffer::from_bytes(&offer_bytes).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid sync offer: {e}"))
        })?;

        // Get full payload first
        let full_payload = crate::sync::entries_missing(self.backend.oplog(), &offer);

        // Filter entries: keep those matching node_types + all ancestors
        let type_set: HashSet<String> = node_types.into_iter().collect();

        // Phase 1: collect entries that match the filter.
        // Aggressively exclude: AddNode of wrong type, edges referencing
        // wrong-type nodes, and property updates on wrong-type nodes.
        // Build a set of node_ids we want to keep.
        let mut wanted_node_ids: HashSet<String> = HashSet::new();
        for entry in &full_payload.entries {
            if let GraphOp::AddNode {
                node_id, node_type, ..
            } = &entry.payload
            {
                if type_set.contains(node_type) {
                    wanted_node_ids.insert(node_id.clone());
                }
            }
        }

        let mut keep: HashSet<Hash> = HashSet::new();
        for entry in &full_payload.entries {
            let dominated = match &entry.payload {
                GraphOp::AddNode { node_type, .. } => !type_set.contains(node_type),
                GraphOp::AddEdge {
                    source_id,
                    target_id,
                    ..
                } => !wanted_node_ids.contains(source_id) && !wanted_node_ids.contains(target_id),
                GraphOp::UpdateProperty { entity_id, .. }
                | GraphOp::RemoveNode {
                    node_id: entity_id, ..
                } => !wanted_node_ids.contains(entity_id),
                GraphOp::RemoveEdge { .. } => false, // keep (can't tell without edge lookup)
                GraphOp::DefineOntology { .. }
                | GraphOp::ExtendOntology { .. }
                | GraphOp::Checkpoint { .. } => false, // always keep
            };
            if !dominated {
                keep.insert(entry.hash);
            }
        }

        // Phase 2: causal closure — add all ancestors of kept entries
        // Review 4 fix (issue #9): BFS queue instead of O(n²*d) nested loop.
        // Same pattern as the EXP-01 fix for entries_missing.
        {
            let entry_map: HashMap<Hash, usize> = full_payload
                .entries
                .iter()
                .enumerate()
                .map(|(i, e)| (e.hash, i))
                .collect();
            let mut queue: std::collections::VecDeque<Hash> = keep.iter().copied().collect();
            while let Some(hash) = queue.pop_front() {
                if let Some(&idx) = entry_map.get(&hash) {
                    for parent in &full_payload.entries[idx].next {
                        if !keep.contains(parent) {
                            keep.insert(*parent);
                            queue.push_back(*parent);
                        }
                    }
                }
            }
        }

        // Build filtered payload
        let filtered_entries: Vec<Entry> = full_payload
            .entries
            .into_iter()
            .filter(|e| keep.contains(&e.hash))
            .collect();

        let filtered = crate::sync::SyncPayload {
            entries: filtered_entries,
            need: full_payload.need,
        };

        Ok(filtered.to_bytes())
    }

    /// Merge a sync payload (entries received from a peer) into this store.
    ///
    /// Returns the number of new entries merged. Updates the materialized graph
    /// incrementally for each new entry.
    fn merge_sync_payload(&mut self, payload_bytes: Vec<u8>) -> PyResult<usize> {
        let payload = crate::sync::SyncPayload::from_bytes(&payload_bytes).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid sync payload: {e}"))
        })?;
        self.merge_entries_vec(&payload.entries)
    }

    /// Merge a list of raw entries (as bytes) into this store.
    ///
    /// Lower-level than `merge_sync_payload` — takes entries directly.
    /// Returns the number of new entries merged.
    fn merge_entries_bytes(&mut self, entries_bytes: Vec<u8>) -> PyResult<usize> {
        let entries: Vec<Entry> = rmp_serde::from_slice(&entries_bytes).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid entries bytes: {e}"))
        })?;
        self.merge_entries_vec(&entries)
    }

    /// Generate a full snapshot (all entries) as bytes.
    ///
    /// Used to bootstrap new peers. The new peer calls `load_snapshot` to
    /// create a store from this data.
    fn snapshot(&self) -> PyResult<Vec<u8>> {
        let snap = crate::sync::Snapshot::from_oplog(self.backend.oplog());
        Ok(snap.to_bytes())
    }

    /// Create a new in-memory store from a snapshot (bytes).
    ///
    /// Deserializes the snapshot, rebuilds the op log and materializes the graph.
    #[staticmethod]
    fn from_snapshot(instance_id: String, snapshot_bytes: Vec<u8>) -> PyResult<Self> {
        let snap = crate::sync::Snapshot::from_bytes(&snapshot_bytes).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid snapshot: {e}"))
        })?;

        if snap.entries.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "snapshot contains no entries",
            ));
        }

        // Extract ontology from genesis (DefineOntology or Checkpoint).
        let genesis = &snap.entries[0];
        let ontology = extract_ontology_from_genesis(genesis)?;

        // Build op log from genesis.
        let mut oplog = crate::oplog::OpLog::new(genesis.clone());

        // Merge remaining entries.
        if snap.entries.len() > 1 {
            crate::sync::merge_entries(&mut oplog, &snap.entries[1..]).map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("snapshot merge failed: {e}"))
            })?;
        }

        // Reconstruct node_types and materialized graph.
        let all = oplog.entries_since(None);
        let mut node_types = HashMap::new();
        for entry in &all {
            match &entry.payload {
                GraphOp::AddNode {
                    node_id, node_type, ..
                } => {
                    node_types.insert(node_id.clone(), node_type.clone());
                }
                GraphOp::RemoveNode { node_id } => {
                    node_types.remove(node_id);
                }
                GraphOp::Checkpoint { ops, .. } => {
                    for op in ops {
                        if let GraphOp::AddNode {
                            node_id, node_type, ..
                        } = op
                        {
                            node_types.insert(node_id.clone(), node_type.clone());
                        }
                    }
                }
                _ => {}
            }
        }

        let mut graph = crate::graph::MaterializedGraph::new(ontology.clone());
        let refs: Vec<&Entry> = all.iter().copied().collect();
        graph.rebuild(&refs);
        // R-03: sync ontology from graph (may have been extended).
        let ontology = graph.ontology.clone();

        // Derive clock from the highest physical time in the snapshot.
        let max_physical = all.iter().map(|e| e.clock.physical_ms).max().unwrap_or(0);
        let max_logical = all
            .iter()
            .filter(|e| e.clock.physical_ms == max_physical)
            .map(|e| e.clock.logical)
            .max()
            .unwrap_or(0);
        let clock = LamportClock::with_values(&instance_id, max_physical, max_logical);

        let gossip = crate::gossip::PeerRegistry::with_instance_id(&instance_id);
        Ok(Self {
            backend: Backend::Memory(oplog),
            graph,
            node_types,
            instance_id,
            clock,
            ontology,
            subscribers: Vec::new(),
            next_sub_id: 0,
            #[cfg(feature = "signing")]
            signing_key: None,
            #[cfg(feature = "signing")]
            key_registry: HashMap::new(),
            #[cfg(feature = "signing")]
            require_signatures: false,
            gossip,
        })
    }

    // -- Subscriptions (D-023) --

    /// Register a callback to be notified on every graph mutation.
    /// Returns a subscription ID for unsubscribing.
    fn subscribe(&mut self, callback: PyObject) -> u64 {
        let id = self.next_sub_id;
        self.next_sub_id += 1;
        self.subscribers.push((id, callback));
        id
    }

    /// Remove a previously registered subscription by ID.
    fn unsubscribe(&mut self, sub_id: u64) {
        self.subscribers.retain(|(id, _)| *id != sub_id);
    }

    // -- Time-Travel (R-06) --

    /// R-06: Create a read-only snapshot of the graph at a historical time.
    ///
    /// Review 4 fix (issue #15): builds the ontology from entries within the
    /// time window, not the current (potentially post-extension) ontology.
    /// This ensures quarantine decisions match the ontology state at that time.
    #[pyo3(signature = (physical_ms, logical=0))]
    fn as_of(&self, physical_ms: u64, logical: u32) -> PyResult<snapshot::PyGraphSnapshot> {
        let entries = self.backend.oplog().entries_as_of(physical_ms, logical);

        // Build ontology from entries within the time window only.
        // Start with the genesis ontology, then apply ExtendOntology entries
        // that fall within the cutoff. This ensures the historical snapshot
        // uses the ontology as it existed at that time.
        let mut ontology: Option<Ontology> = None;
        for entry in &entries {
            match &entry.payload {
                GraphOp::DefineOntology { ontology: ont } => {
                    ontology = Some(ont.clone());
                }
                GraphOp::Checkpoint { ops, .. } => {
                    for op in ops {
                        if let GraphOp::DefineOntology { ontology: ont } = op {
                            ontology = Some(ont.clone());
                            break;
                        }
                    }
                }
                GraphOp::ExtendOntology { extension } => {
                    if let Some(ref mut ont) = ontology {
                        let _ = ont.merge_extension(extension);
                    }
                }
                _ => {}
            }
        }

        // If no ontology found in the time window (cutoff before genesis),
        // fall back to the current genesis ontology for an empty snapshot.
        let ontology = match ontology {
            Some(ont) => ont,
            None => {
                let all = self.backend.oplog().entries_since(None);
                let genesis = all.first().ok_or_else(|| {
                    pyo3::exceptions::PyRuntimeError::new_err("no entries in oplog")
                })?;
                extract_ontology_from_genesis(genesis)?
            }
        };

        let mut graph = MaterializedGraph::new(ontology);
        let refs: Vec<&Entry> = entries.iter().copied().collect();
        graph.rebuild(&refs);

        Ok(snapshot::PyGraphSnapshot {
            graph,
            cutoff_clock: (physical_ms, logical),
            instance_id: self.instance_id.clone(),
        })
    }

    // -- Quarantine (R-02) --

    /// Get the list of quarantined entry hashes (hex-encoded).
    /// Quarantined entries are in the oplog (for CRDT convergence) but
    /// invisible in the materialized graph (failed ontology validation).
    fn get_quarantined(&self) -> Vec<String> {
        self.graph
            .quarantined
            .iter()
            .map(|h| hex::encode(h))
            .collect()
    }

    // -- Gossip Peer Selection (R-05) --

    /// Register a peer for gossip sync.
    fn register_peer(&mut self, peer_id: String, address: String) {
        self.gossip.register(peer_id, address);
    }

    /// Unregister a peer.
    fn unregister_peer(&mut self, peer_id: &str) -> bool {
        self.gossip.unregister(peer_id)
    }

    /// List all registered peers.
    fn list_peers(&self, py: Python<'_>) -> PyResult<PyObject> {
        let list = pyo3::types::PyList::empty(py);
        for peer in self.gossip.list() {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("peer_id", &peer.peer_id)?;
            dict.set_item("address", &peer.address)?;
            dict.set_item("last_seen_ms", peer.last_seen_ms)?;
            list.append(dict)?;
        }
        Ok(list.into())
    }

    /// Select sync targets for this round (ceil(ln(N) + 1) random peers).
    fn select_sync_targets(&self) -> Vec<String> {
        self.gossip.select_sync_targets()
    }

    /// Record that a sync with a peer completed.
    fn record_sync(&mut self, peer_id: &str) {
        self.gossip.record_sync(peer_id);
    }

    /// Check if compaction is safe: all known peers must have synced
    /// since the most recent entry. Returns (safe, reasons).
    fn verify_compaction_safe(&self) -> (bool, Vec<String>) {
        // Find the latest entry's physical clock
        let all = self.backend.oplog().entries_since(None);
        let latest_ms = all.iter().map(|e| e.clock.physical_ms).max().unwrap_or(0);
        self.gossip.verify_compaction_safe(latest_ms)
    }

    // -- Signing (D-027) --

    /// Generate a new random ed25519 keypair, store the private key, return hex public key.
    #[cfg(feature = "signing")]
    fn generate_signing_key(&mut self) -> String {
        use rand::rngs::OsRng;
        let key = ed25519_dalek::SigningKey::generate(&mut OsRng);
        let public_hex = hex::encode(key.verifying_key().as_bytes());
        self.signing_key = Some(key);
        public_hex
    }

    /// Load an existing private key from hex (64 hex chars = 32 bytes).
    #[cfg(feature = "signing")]
    fn set_signing_key(&mut self, hex_private_key: &str) -> PyResult<()> {
        let bytes = hex::decode(hex_private_key)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid hex: {e}")))?;
        if bytes.len() != 32 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "signing key must be 32 bytes, got {}",
                bytes.len()
            )));
        }
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&bytes);
        self.signing_key = Some(ed25519_dalek::SigningKey::from_bytes(&arr));
        Ok(())
    }

    /// Get the public key as hex (64 hex chars), None if no key set.
    #[cfg(feature = "signing")]
    fn get_public_key(&self) -> Option<String> {
        self.signing_key
            .as_ref()
            .map(|k| hex::encode(k.verifying_key().as_bytes()))
    }

    /// Register a trusted author's public key for signature verification.
    #[cfg(feature = "signing")]
    fn register_trusted_author(&mut self, author_id: String, hex_public_key: &str) -> PyResult<()> {
        let bytes = hex::decode(hex_public_key)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid hex: {e}")))?;
        if bytes.len() != 32 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "public key must be 32 bytes, got {}",
                bytes.len()
            )));
        }
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&bytes);
        let vk = ed25519_dalek::VerifyingKey::from_bytes(&arr).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid ed25519 public key: {e}"))
        })?;
        self.key_registry.insert(author_id, vk);
        Ok(())
    }

    /// Toggle strict mode: reject unsigned entries on merge when enabled.
    #[cfg(feature = "signing")]
    fn set_require_signatures(&mut self, enabled: bool) {
        self.require_signatures = enabled;
    }

    // -- Epoch Compaction (R-08) --

    /// R-08: Create a checkpoint entry from the current graph state.
    /// Returns the checkpoint as bytes (for inspection), does NOT compact yet.
    fn create_checkpoint(&self) -> PyResult<Vec<u8>> {
        let (ops, clocks) = self.build_checkpoint_ops();
        let op_clocks: Vec<(u64, u32)> = clocks.iter().map(|c| c.as_tuple()).collect();
        let (phys, log) = self.clock.as_tuple();
        let checkpoint_op = GraphOp::Checkpoint {
            ops,
            op_clocks,
            compacted_at_physical_ms: phys,
            compacted_at_logical: log,
        };
        let entry = self.create_entry(
            checkpoint_op,
            vec![],
            vec![],
            self.clock.clone(),
            &self.instance_id,
        );
        Ok(entry.to_bytes())
    }

    /// R-08: Compact the oplog. Creates a checkpoint of current state,
    /// replaces entire oplog with the checkpoint entry.
    /// Returns the hex hash of the checkpoint entry.
    /// SAFETY: Only call when ALL peers have synced to current state.
    /// Compact the oplog into a single checkpoint entry.
    /// If `safe` is true (default), verifies all known peers have synced
    /// before compacting. Returns the checkpoint hash.
    #[pyo3(signature = (safe = true))]
    fn compact(&mut self, safe: bool) -> PyResult<String> {
        if safe {
            let (is_safe, reasons) = self.verify_compaction_safe();
            if !is_safe {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "compaction is unsafe: {}. Pass safe=False to force.",
                    reasons.join("; ")
                )));
            }
        }
        let (ops, clocks) = self.build_checkpoint_ops();
        let op_clocks: Vec<(u64, u32)> = clocks.iter().map(|c| c.as_tuple()).collect();
        self.clock.tick();
        let (phys, log) = self.clock.as_tuple();
        let checkpoint = self.create_entry(
            GraphOp::Checkpoint {
                ops,
                op_clocks,
                compacted_at_physical_ms: phys,
                compacted_at_logical: log,
            },
            vec![], // new genesis — no predecessors
            vec![],
            self.clock.clone(),
            &self.instance_id,
        );
        let hash_hex = hex::encode(checkpoint.hash);

        match &mut self.backend {
            Backend::Memory(oplog) => oplog.replace_with_checkpoint(checkpoint),
            Backend::Persistent(store) => {
                store
                    .replace_with_checkpoint(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            }
        }

        Ok(hash_hex)
    }
}

impl PyGraphStore {
    /// R-08: Build synthetic ops that reconstruct the current graph state.
    /// Order: DefineOntology (with all extensions merged), AddNode for each live node, AddEdge for each live edge.
    /// Build synthetic ops + per-entity clocks for checkpoint.
    /// Returns (ops, clocks) where clocks[i] is the clock to use for ops[i].
    /// Bug 6 fix: each entity uses its max per-property clock, not the checkpoint clock.
    fn build_checkpoint_ops(&self) -> (Vec<GraphOp>, Vec<LamportClock>) {
        let mut ops = Vec::new();
        let mut clocks = Vec::new();

        // 1. Ontology (with all extensions merged)
        ops.push(GraphOp::DefineOntology {
            ontology: self.ontology.clone(),
        });
        clocks.push(self.clock.clone());

        // 2. All live nodes
        // EXP-02 fix: emit AddNode with last_add_clock (for add-wins) and
        // empty properties, then individual UpdateProperty ops with per-property
        // clocks. This preserves per-property LWW granularity through compaction.
        for node in self.graph.all_nodes() {
            // AddNode with last_add_clock — establishes entity + add-wins clock
            ops.push(GraphOp::AddNode {
                node_id: node.node_id.clone(),
                node_type: node.node_type.clone(),
                subtype: node.subtype.clone(),
                label: node.label.clone(),
                properties: BTreeMap::new(),
            });
            clocks.push(node.last_add_clock.clone());

            // Per-property UpdateProperty ops with individual clocks
            for (key, value) in &node.properties {
                let prop_clock = node
                    .property_clocks
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| node.last_clock.clone());
                ops.push(GraphOp::UpdateProperty {
                    entity_id: node.node_id.clone(),
                    key: key.clone(),
                    value: value.clone(),
                });
                clocks.push(prop_clock);
            }
        }

        // 3. All live edges — same pattern: AddEdge + per-property updates
        for edge in self.graph.all_edges() {
            ops.push(GraphOp::AddEdge {
                edge_id: edge.edge_id.clone(),
                edge_type: edge.edge_type.clone(),
                source_id: edge.source_id.clone(),
                target_id: edge.target_id.clone(),
                properties: BTreeMap::new(),
            });
            clocks.push(edge.last_add_clock.clone());

            for (key, value) in &edge.properties {
                let prop_clock = edge
                    .property_clocks
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| edge.last_clock.clone());
                ops.push(GraphOp::UpdateProperty {
                    entity_id: edge.edge_id.clone(),
                    key: key.clone(),
                    value: value.clone(),
                });
                clocks.push(prop_clock);
            }
        }

        (ops, clocks)
    }

    /// Validate an entry's payload against the ontology (used by graph.apply() for R-02 quarantine).
    /// Returns Ok(()) if valid (or not applicable), Err(reason) if invalid.
    fn validate_entry_payload(&self, entry: &Entry) -> Result<(), String> {
        match &entry.payload {
            GraphOp::AddNode {
                node_type,
                subtype,
                properties,
                ..
            } => self
                .ontology
                .validate_node(node_type, subtype.as_deref(), properties)
                .map_err(|e| e.to_string()),
            GraphOp::AddEdge { edge_type, .. } => {
                // Full edge validation requires source/target node types which
                // may not be available yet during sync. Just check edge_type exists.
                if self.ontology.edge_types.contains_key(edge_type) {
                    Ok(())
                } else {
                    Err(format!("unknown edge type '{edge_type}'"))
                }
            }
            GraphOp::UpdateProperty {
                entity_id,
                key,
                value,
            } => {
                // Validate property type against ontology (if node is known)
                if let Some(node) = self.graph.get_node(entity_id) {
                    self.ontology
                        .validate_property_update(
                            &node.node_type,
                            node.subtype.as_deref(),
                            key,
                            value,
                        )
                        .map_err(|e| e.to_string())
                } else {
                    Ok(()) // Node not yet materialized (e.g., during sync)
                }
            }
            // RemoveNode, RemoveEdge, DefineOntology: no validation needed.
            _ => Ok(()),
        }
    }

    /// Maximum clock drift allowed from a remote peer.
    /// Entries with clock times exceeding local_clock + MAX_CLOCK_DRIFT are rejected.
    /// Prevents the "Byzantine clock" attack where a malicious peer sets clock to
    /// u64::MAX to permanently win all LWW conflicts.
    const MAX_CLOCK_DRIFT: u64 = 1_000_000;

    /// Merge a vec of entries into the store, updating the materialized graph.
    fn merge_entries_vec(&mut self, entries: &[Entry]) -> PyResult<usize> {
        let local_physical = self.clock.physical_ms;

        // R-02: Ontology validation moved to graph.apply() (quarantine model).
        // Only security checks remain here: clock drift + signature verification.
        let valid_entries: Vec<Entry> = entries
            .iter()
            .filter(|e| {
                // Clock drift check (skip for genesis/DefineOntology entries)
                if !matches!(e.payload, GraphOp::DefineOntology { .. })
                    && e.clock.physical_ms > local_physical.saturating_add(Self::MAX_CLOCK_DRIFT)
                {
                    eprintln!(
                        "silk: rejecting sync entry {}: physical_ms {} exceeds local {} + drift {}",
                        hex::encode(e.hash),
                        e.clock.physical_ms,
                        local_physical,
                        Self::MAX_CLOCK_DRIFT
                    );
                    return false;
                }
                // D-027: Signature verification (skip for genesis/DefineOntology entries)
                #[cfg(feature = "signing")]
                {
                    let is_genesis = matches!(e.payload, GraphOp::DefineOntology { .. });
                    if self.require_signatures && !is_genesis {
                        if !e.is_signed() {
                            eprintln!(
                                "silk: rejecting sync entry {}: unsigned (require_signatures=true)",
                                hex::encode(e.hash),
                            );
                            return false;
                        }
                        if let Some(vk) = self.key_registry.get(&e.author) {
                            if !e.verify_signature(vk) {
                                eprintln!(
                                    "silk: rejecting sync entry {}: signature verification failed for author '{}'",
                                    hex::encode(e.hash),
                                    e.author,
                                );
                                return false;
                            }
                        } else {
                            eprintln!(
                                "silk: rejecting sync entry {}: unknown author '{}' (not in key registry)",
                                hex::encode(e.hash),
                                e.author,
                            );
                            return false;
                        }
                    } else if e.is_signed() {
                        // Best-effort: verify if author is in registry, skip if not
                        if let Some(vk) = self.key_registry.get(&e.author) {
                            if !e.verify_signature(vk) {
                                eprintln!(
                                    "silk: rejecting sync entry {}: signature verification failed for author '{}'",
                                    hex::encode(e.hash),
                                    e.author,
                                );
                                return false;
                            }
                        }
                    }
                }
                true
            })
            .cloned()
            .collect();

        // Review 4 fix (issue #10): track candidate hashes from the payload
        // instead of walking the entire DAG to build an "existing" set.
        // The candidates are exactly the entries we're about to merge.
        let candidate_hashes: HashSet<Hash> = valid_entries.iter().map(|e| e.hash).collect();

        // Merge into oplog (and redb for persistent backend).
        let inserted = match &mut self.backend {
            Backend::Memory(oplog) => crate::sync::merge_entries(oplog, &valid_entries)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?,
            Backend::Persistent(store) => store
                .merge(&valid_entries)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?,
        };

        if inserted > 0 {
            let all = self.backend.oplog().entries_since(None);

            // Bug 5 fix: check if any new entry is ExtendOntology or Checkpoint.
            // A new entry = one from our candidate set that's now in the oplog.
            let has_schema_change = valid_entries.iter().any(|e| {
                candidate_hashes.contains(&e.hash)
                    && matches!(
                        e.payload,
                        GraphOp::ExtendOntology { .. } | GraphOp::Checkpoint { .. }
                    )
            });

            if has_schema_change {
                // Review 4: capture quarantine set before rebuild
                let quarantined_before = self.graph.quarantined.clone();

                // Full rebuild: deterministic topo order → identical quarantine sets
                let refs: Vec<&Entry> = all.iter().copied().collect();
                self.graph.rebuild(&refs);
                // Rebuild node_types and ontology from the rebuilt graph
                self.node_types.clear();
                for node in self.graph.all_nodes() {
                    self.node_types
                        .insert(node.node_id.clone(), node.node_type.clone());
                }
                self.ontology = self.graph.ontology.clone();

                // Review 4: notify subscribers for entries that were un-quarantined
                // (previously quarantined but now valid after ontology evolution)
                for hash in &quarantined_before {
                    if !self.graph.quarantined.contains(hash) {
                        // Entry was un-quarantined — find it and notify
                        if let Some(entry) = all.iter().find(|e| e.hash == *hash) {
                            self.notify_subscribers(entry, false);
                        }
                    }
                }
            } else {
                // Incremental apply: safe when no schema changes
                for entry in &all {
                    if candidate_hashes.contains(&entry.hash) {
                        self.graph.apply(entry);
                        match &entry.payload {
                            GraphOp::AddNode {
                                node_id, node_type, ..
                            } => {
                                self.node_types.insert(node_id.clone(), node_type.clone());
                            }
                            GraphOp::RemoveNode { node_id } => {
                                self.node_types.remove(node_id);
                            }
                            _ => {}
                        }
                    }
                }
            }

            // Update clock and notify subscribers for all new entries
            for entry in &all {
                if candidate_hashes.contains(&entry.hash) {
                    self.clock.merge(&entry.clock);
                    self.notify_subscribers(entry, false);
                }
            }
        }

        Ok(inserted)
    }

    /// Create an entry, auto-signing if a signing key is configured.
    fn create_entry(
        &self,
        payload: GraphOp,
        next: Vec<Hash>,
        refs: Vec<Hash>,
        clock: LamportClock,
        author: &str,
    ) -> Entry {
        #[cfg(feature = "signing")]
        {
            if let Some(ref key) = self.signing_key {
                return Entry::new_signed(payload, next, refs, clock, author, key);
            }
        }
        Entry::new(payload, next, refs, clock, author)
    }

    pub(crate) fn append(&mut self, op: GraphOp) -> PyResult<String> {
        self.clock.tick();
        let heads = self.backend.oplog().heads();
        let entry = self.create_entry(op, heads, vec![], self.clock.clone(), &self.instance_id);
        let hex = hex::encode(entry.hash);
        // Validate against ontology before applying (type checks, constraints)
        if let Err(reason) = self.validate_entry_payload(&entry) {
            return Err(pyo3::exceptions::PyValueError::new_err(reason));
        }
        // Review 4 fix (issue #11): persist before apply.
        // If persist fails (I/O error), the graph is unchanged.
        // Previously: graph was modified before persist, creating
        // inconsistent state on I/O failure.
        self.backend
            .append(entry.clone())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        self.graph.apply(&entry);
        // D-023: notify subscribers (local write → local=true)
        self.notify_subscribers(&entry, true);
        Ok(hex)
    }

    /// Notify all registered subscribers with an event dict for the given entry.
    /// Exceptions in callbacks are logged and swallowed (D-023: error isolation).
    fn notify_subscribers(&self, entry: &Entry, is_local: bool) {
        if self.subscribers.is_empty() {
            return;
        }
        Python::with_gil(|py| {
            let event = Self::entry_to_event_dict(py, entry, is_local);
            for (_id, callback) in &self.subscribers {
                if let Err(e) = callback.call1(py, (&event,)) {
                    // D-023: error isolation — log and continue
                    eprintln!("silk: subscriber error: {e}");
                }
            }
        });
    }

    /// Build a Python dict from an Entry for subscription callbacks.
    fn entry_to_event_dict(py: Python<'_>, entry: &Entry, is_local: bool) -> PyObject {
        let dict = PyDict::new(py);
        let _ = dict.set_item("hash", hex::encode(entry.hash));
        let _ = dict.set_item("author", &entry.author);
        let _ = dict.set_item("physical_ms", entry.clock.physical_ms);
        let _ = dict.set_item("logical", entry.clock.logical);
        let _ = dict.set_item("local", is_local);

        match &entry.payload {
            GraphOp::AddNode {
                node_id,
                node_type,
                subtype,
                ..
            } => {
                let _ = dict.set_item("op", "add_node");
                let _ = dict.set_item("node_id", node_id);
                let _ = dict.set_item("node_type", node_type);
                match subtype {
                    Some(st) => {
                        let _ = dict.set_item("subtype", st);
                    }
                    None => {
                        let _ = dict.set_item("subtype", py.None());
                    }
                }
            }
            GraphOp::AddEdge {
                edge_id,
                edge_type,
                source_id,
                target_id,
                ..
            } => {
                let _ = dict.set_item("op", "add_edge");
                let _ = dict.set_item("edge_id", edge_id);
                let _ = dict.set_item("edge_type", edge_type);
                let _ = dict.set_item("source_id", source_id);
                let _ = dict.set_item("target_id", target_id);
            }
            GraphOp::UpdateProperty {
                entity_id,
                key,
                value,
            } => {
                let _ = dict.set_item("op", "update_property");
                let _ = dict.set_item("entity_id", entity_id);
                let _ = dict.set_item("key", key);
                if let Ok(py_val) = value_to_py(py, value) {
                    let _ = dict.set_item("value", py_val);
                }
            }
            GraphOp::RemoveNode { node_id } => {
                let _ = dict.set_item("op", "remove_node");
                let _ = dict.set_item("node_id", node_id);
            }
            GraphOp::RemoveEdge { edge_id } => {
                let _ = dict.set_item("op", "remove_edge");
                let _ = dict.set_item("edge_id", edge_id);
            }
            GraphOp::DefineOntology { .. } => {
                let _ = dict.set_item("op", "define_ontology");
            }
            GraphOp::ExtendOntology { .. } => {
                let _ = dict.set_item("op", "extend_ontology");
            }
            GraphOp::Checkpoint { .. } => {
                let _ = dict.set_item("op", "checkpoint");
            }
        }
        dict.into()
    }
}

/// Convert a serde_json::Value to a Python object.
/// Uses JSON string as intermediate — simple, correct, no PyO3 version issues.
fn json_value_to_py(py: Python<'_>, val: &serde_json::Value) -> PyResult<PyObject> {
    let json_str = serde_json::to_string(val)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("serialize: {e}")))?;
    let json_mod = py.import("json")?;
    let result = json_mod.call_method1("loads", (json_str,))?;
    Ok(result.unbind())
}

// -- OperationBuffer (pre-store write-ahead buffer) --

/// Filesystem-backed buffer for graph operations.
///
/// Stores operations as JSONL when the GraphStore isn't available yet
/// (e.g., boot time). Drain into a live store when it becomes available.
///
/// Operations are raw payloads — no hash, no clock, no DAG parents.
/// These are assigned at drain time through the normal store API.
/// Ontology validation, subscriptions, and HLC timestamps all happen at drain.
#[pyclass(name = "OperationBuffer")]
struct PyOperationBuffer {
    inner: crate::buffer::OperationBuffer,
}

#[pymethods]
impl PyOperationBuffer {
    #[new]
    fn new(path: String) -> Self {
        Self {
            inner: crate::buffer::OperationBuffer::new(path),
        }
    }

    /// Buffer an add_node operation.
    #[pyo3(signature = (node_id, node_type, label, properties=None, subtype=None))]
    fn add_node(
        &self,
        node_id: String,
        node_type: String,
        label: String,
        properties: Option<&pyo3::Bound<'_, PyDict>>,
        subtype: Option<String>,
    ) -> PyResult<()> {
        let props = match properties {
            Some(d) => convert_props(Some(d))?,
            None => BTreeMap::new(),
        };
        let op = GraphOp::AddNode {
            node_id,
            node_type,
            subtype,
            label,
            properties: props,
        };
        self.inner
            .append(&op)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))
    }

    /// Buffer an add_edge operation.
    #[pyo3(signature = (edge_id, edge_type, source_id, target_id, properties=None))]
    fn add_edge(
        &self,
        edge_id: String,
        edge_type: String,
        source_id: String,
        target_id: String,
        properties: Option<&pyo3::Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        let props = match properties {
            Some(d) => convert_props(Some(d))?,
            None => BTreeMap::new(),
        };
        let op = GraphOp::AddEdge {
            edge_id,
            edge_type,
            source_id,
            target_id,
            properties: props,
        };
        self.inner
            .append(&op)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))
    }

    /// Buffer an update_property operation.
    fn update_property(
        &self,
        entity_id: String,
        key: String,
        value: &pyo3::Bound<'_, pyo3::PyAny>,
    ) -> PyResult<()> {
        let val = py_to_value(value)?;
        let op = GraphOp::UpdateProperty {
            entity_id,
            key,
            value: val,
        };
        self.inner
            .append(&op)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))
    }

    /// Buffer a remove_node operation.
    fn remove_node(&self, node_id: String) -> PyResult<()> {
        let op = GraphOp::RemoveNode { node_id };
        self.inner
            .append(&op)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))
    }

    /// Buffer a remove_edge operation.
    fn remove_edge(&self, edge_id: String) -> PyResult<()> {
        let op = GraphOp::RemoveEdge { edge_id };
        self.inner
            .append(&op)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))
    }

    /// Drain all buffered operations into a live store.
    ///
    /// Each operation is applied through the store's internal append():
    /// ontology validation, HLC clock, DAG parents, subscriptions all fire.
    /// After successful drain, the buffer is cleared.
    ///
    /// Returns the number of operations applied.
    fn drain(&self, store: &mut PyGraphStore) -> PyResult<usize> {
        let ops = self
            .inner
            .read_all()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))?;
        let count = ops.len();
        for op in &ops {
            match op {
                GraphOp::DefineOntology { .. }
                | GraphOp::ExtendOntology { .. }
                | GraphOp::Checkpoint { .. } => {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "buffer contains non-bufferable operation",
                    ));
                }
                GraphOp::AddNode {
                    node_id, node_type, ..
                } => {
                    store.node_types.insert(node_id.clone(), node_type.clone());
                    store.append(op.clone())?;
                }
                GraphOp::RemoveNode { node_id } => {
                    store.node_types.remove(node_id);
                    store.append(op.clone())?;
                }
                _ => {
                    store.append(op.clone())?;
                }
            }
        }
        self.inner
            .clear()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))?;
        Ok(count)
    }

    /// Read all buffered operations as Python dicts (without draining).
    ///
    /// Each dict has an "op" key ("add_node", "update_property", etc.)
    /// plus the operation's fields. Useful for inspecting the buffer
    /// before draining (e.g., counting boot events for crash loop detection).
    fn read_all(&self, py: Python<'_>) -> PyResult<PyObject> {
        let ops = self
            .inner
            .read_all()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e))?;
        let list = PyList::empty(py);
        for op in &ops {
            let json_val = serde_json::to_value(op).map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("serialize: {e}"))
            })?;
            let py_obj = json_value_to_py(py, &json_val)?;
            list.append(py_obj)?;
        }
        Ok(list.into())
    }

    /// Number of buffered operations.
    fn __len__(&self) -> usize {
        self.inner.len()
    }

    /// Path to the buffer file.
    #[getter]
    fn path(&self) -> String {
        self.inner.path().to_string_lossy().to_string()
    }
}

pub fn register(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_class::<PyGraphStore>()?;
    m.add_class::<snapshot::PyGraphSnapshot>()?;
    m.add_class::<obslog::PyObservationLog>()?;
    m.add_class::<PyOperationBuffer>()?;
    Ok(())
}
