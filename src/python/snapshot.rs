//! PyGraphSnapshot — R-06: read-only historical graph snapshot.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::engine;
use crate::graph::MaterializedGraph;

use super::conversions::{edge_to_pydict, node_to_pydict, py_to_value};

/// R-06: Read-only snapshot of the graph at a historical point in time.
/// Created by `GraphStore.as_of(physical_ms, logical)`.
#[pyclass]
pub struct PyGraphSnapshot {
    pub(crate) graph: MaterializedGraph,
    pub(crate) cutoff_clock: (u64, u32),
    pub(crate) instance_id: String,
}

#[pymethods]
impl PyGraphSnapshot {
    /// The cutoff clock used to create this snapshot: (physical_ms, logical).
    fn cutoff_clock(&self) -> (u64, u32) {
        self.cutoff_clock
    }

    /// Instance identifier of the store that created this snapshot.
    fn instance_id(&self) -> &str {
        &self.instance_id
    }

    // -- Graph queries (read-only) --

    fn get_node(&self, py: Python<'_>, node_id: &str) -> PyResult<Option<PyObject>> {
        Ok(self
            .graph
            .get_node(node_id)
            .map(|n| node_to_pydict(py, n).unwrap()))
    }

    fn get_edge(&self, py: Python<'_>, edge_id: &str) -> PyResult<Option<PyObject>> {
        Ok(self
            .graph
            .get_edge(edge_id)
            .map(|e| edge_to_pydict(py, e).unwrap()))
    }

    fn query_nodes_by_type(&self, py: Python<'_>, node_type: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .nodes_by_type(node_type)
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    fn query_nodes_by_subtype(&self, py: Python<'_>, subtype: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .nodes_by_subtype(subtype)
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

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

    fn all_nodes(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.graph
            .all_nodes()
            .iter()
            .map(|n| node_to_pydict(py, n))
            .collect()
    }

    fn all_edges(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.graph
            .all_edges()
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    fn outgoing_edges(&self, py: Python<'_>, node_id: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .outgoing_edges(node_id)
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    fn incoming_edges(&self, py: Python<'_>, node_id: &str) -> PyResult<Vec<PyObject>> {
        self.graph
            .incoming_edges(node_id)
            .iter()
            .map(|e| edge_to_pydict(py, e))
            .collect()
    }

    fn neighbors(&self, node_id: &str) -> Vec<String> {
        self.graph
            .neighbors(node_id)
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    // -- Engine methods --

    #[pyo3(signature = (start, max_depth=None, edge_type=None))]
    fn bfs(&self, start: &str, max_depth: Option<usize>, edge_type: Option<&str>) -> Vec<String> {
        engine::bfs(&self.graph, start, max_depth, edge_type)
    }

    fn shortest_path(&self, start: &str, end: &str) -> Option<Vec<String>> {
        engine::shortest_path(&self.graph, start, end)
    }

    #[pyo3(signature = (node_id, max_depth=None))]
    fn impact_analysis(&self, node_id: &str, max_depth: Option<usize>) -> Vec<String> {
        engine::impact_analysis(&self.graph, node_id, max_depth)
    }

    fn subgraph(&self, py: Python<'_>, start: &str, hops: usize) -> PyResult<PyObject> {
        let (nodes, edges) = engine::subgraph(&self.graph, start, hops);
        let dict = PyDict::new(py);
        dict.set_item("nodes", nodes)?;
        dict.set_item("edges", edges)?;
        Ok(dict.into())
    }

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

    fn topological_sort(&self) -> Option<Vec<String>> {
        engine::topological_sort(&self.graph)
    }

    fn has_cycle(&self) -> bool {
        engine::has_cycle(&self.graph)
    }
}
