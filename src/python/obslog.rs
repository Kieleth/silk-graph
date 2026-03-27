//! PyObservationLog — append-only, TTL-pruned observation store (D-025).

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::{BTreeMap, HashMap};

/// D-025: Append-only time-series observation log with TTL pruning.
#[pyclass(name = "ObservationLog")]
pub struct PyObservationLog {
    log: crate::obslog::ObservationLog,
}

#[pymethods]
impl PyObservationLog {
    #[new]
    #[pyo3(signature = (path, max_age_secs = 86400))]
    fn new(path: &str, max_age_secs: u64) -> PyResult<Self> {
        let log = crate::obslog::ObservationLog::open(std::path::Path::new(path), max_age_secs)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
        Ok(Self { log })
    }

    /// Append a single observation.
    #[pyo3(signature = (source, value, metadata = None))]
    fn append(
        &self,
        source: &str,
        value: f64,
        metadata: Option<HashMap<String, String>>,
    ) -> PyResult<()> {
        let meta: BTreeMap<String, String> = metadata.unwrap_or_default().into_iter().collect();
        self.log
            .append(source, value, meta)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }

    /// Query observations for a source since a timestamp (milliseconds).
    fn query(&self, py: Python<'_>, source: &str, since_ts_ms: u64) -> PyResult<Vec<PyObject>> {
        let obs = self
            .log
            .query(source, since_ts_ms)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
        obs.iter().map(|o| obs_to_pydict(py, o)).collect()
    }

    /// Get the most recent observation for a source.
    fn query_latest(&self, py: Python<'_>, source: &str) -> PyResult<Option<PyObject>> {
        let obs = self
            .log
            .query_latest(source)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
        match obs {
            Some(o) => Ok(Some(obs_to_pydict(py, &o)?)),
            None => Ok(None),
        }
    }

    /// List distinct source names.
    fn sources(&self) -> PyResult<Vec<String>> {
        self.log
            .sources()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }

    /// Delete observations older than before_ts_ms. Returns count deleted.
    fn truncate(&self, before_ts_ms: u64) -> PyResult<u64> {
        self.log
            .truncate(before_ts_ms)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }

    /// Total observation count.
    fn count(&self) -> PyResult<u64> {
        self.log
            .count()
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }
}

fn obs_to_pydict(py: Python<'_>, obs: &crate::obslog::Observation) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("timestamp_ms", obs.timestamp_ms)?;
    dict.set_item("source", &obs.source)?;
    dict.set_item("value", obs.value)?;
    let meta = PyDict::new(py);
    for (k, v) in &obs.metadata {
        meta.set_item(k, v)?;
    }
    dict.set_item("metadata", meta)?;
    Ok(dict.into())
}
