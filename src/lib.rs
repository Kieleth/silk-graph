pub mod bloom;
pub mod clock;
pub mod engine;
pub mod entry;
pub mod graph;
pub mod obslog;
pub mod ontology;
pub mod oplog;
#[cfg(feature = "python")]
mod python;
pub mod store;
pub mod sync;

// Re-exports for ergonomic Rust usage
pub use bloom::BloomFilter;
pub use clock::LamportClock;
pub use entry::{Entry, GraphOp, Hash, Value};
pub use graph::{Edge, MaterializedGraph, Node};
pub use obslog::{ObsLogError, Observation, ObservationLog};
pub use ontology::{EdgeTypeDef, NodeTypeDef, Ontology, PropertyDef, ValidationError, ValueType};
pub use oplog::{OpLog, OpLogError};
pub use store::{Store, StoreError};
pub use sync::{Snapshot, SyncOffer, SyncPayload};

/// PyO3 module entry point — called by Python when `import silk._native`.
#[cfg(feature = "python")]
#[pyo3::pymodule]
fn _native(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    python::register(m)?;
    Ok(())
}
