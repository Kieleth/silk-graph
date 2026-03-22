pub mod bloom;
pub mod clock;
pub mod engine;
pub mod entry;
pub mod graph;
pub mod obslog;
pub mod ontology;
pub mod oplog;
pub mod store;
pub mod sync;
#[cfg(feature = "python")]
mod python;

// Re-exports for ergonomic Rust usage
pub use bloom::BloomFilter;
pub use clock::LamportClock;
pub use entry::{Entry, GraphOp, Hash, Value};
pub use graph::{MaterializedGraph, Node, Edge};
pub use ontology::{EdgeTypeDef, NodeTypeDef, Ontology, PropertyDef, ValidationError, ValueType};
pub use oplog::{OpLog, OpLogError};
pub use obslog::{ObservationLog, Observation, ObsLogError};
pub use store::{Store, StoreError};
pub use sync::{SyncOffer, SyncPayload, Snapshot};

/// PyO3 module entry point — called by Python when `import silk._native`.
#[cfg(feature = "python")]
#[pyo3::pymodule]
fn _native(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    python::register(m)?;
    Ok(())
}
