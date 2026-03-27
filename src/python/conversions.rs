//! Python ↔ Rust type conversion helpers.
//!
//! Pure FFI utilities: py_to_value, value_to_py, dict builders.
//! No dependencies on store state.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::BTreeMap;

use crate::entry::{Entry, GraphOp, Hash, Value};
use crate::ontology::Ontology;

// S-10: max nesting depth for py_to_value / value_to_py to prevent stack overflow.
const MAX_VALUE_DEPTH: usize = 64;
// S-12: size limits for values coming from Python.
const MAX_STRING_BYTES: usize = 1_048_576; // 1 MB
const MAX_LIST_ITEMS: usize = 10_000;
const MAX_MAP_ENTRIES: usize = 10_000;

/// Convert a Python dict or JSON string to an ontology JSON string.
pub fn ontology_arg_to_json(obj: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<String> {
    if let Ok(s) = obj.extract::<String>() {
        Ok(s)
    } else if let Ok(dict) = obj.downcast::<PyDict>() {
        let json = serde_json::to_string(&py_dict_to_json(dict)?)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        Ok(json)
    } else {
        Err(pyo3::exceptions::PyTypeError::new_err(
            "ontology must be a JSON string or a dict",
        ))
    }
}

fn py_dict_to_json(dict: &Bound<'_, PyDict>) -> PyResult<serde_json::Value> {
    let mut map = serde_json::Map::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        let val = py_any_to_json(&v)?;
        map.insert(key, val);
    }
    Ok(serde_json::Value::Object(map))
}

fn py_any_to_json(obj: &Bound<'_, pyo3::PyAny>) -> PyResult<serde_json::Value> {
    if obj.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(b) = obj.extract::<bool>() {
        Ok(serde_json::Value::Bool(b))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(serde_json::Value::Number(i.into()))
    } else if let Ok(f) = obj.extract::<f64>() {
        Ok(serde_json::json!(f))
    } else if let Ok(s) = obj.extract::<String>() {
        Ok(serde_json::Value::String(s))
    } else if let Ok(list) = obj.downcast::<pyo3::types::PyList>() {
        let items: PyResult<Vec<serde_json::Value>> =
            list.iter().map(|item| py_any_to_json(&item)).collect();
        Ok(serde_json::Value::Array(items?))
    } else if let Ok(dict) = obj.downcast::<PyDict>() {
        py_dict_to_json(dict)
    } else {
        Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "unsupported type for ontology: {}",
            obj.get_type().name()?
        )))
    }
}

/// Extract ontology from a genesis entry (DefineOntology or Checkpoint).
pub fn extract_ontology_from_genesis(entry: &Entry) -> PyResult<Ontology> {
    match &entry.payload {
        GraphOp::DefineOntology { ontology } => Ok(ontology.clone()),
        GraphOp::Checkpoint { ops, .. } => {
            for op in ops {
                if let GraphOp::DefineOntology { ontology } = op {
                    return Ok(ontology.clone());
                }
            }
            Err(pyo3::exceptions::PyRuntimeError::new_err(
                "checkpoint contains no DefineOntology op",
            ))
        }
        _ => Err(pyo3::exceptions::PyRuntimeError::new_err(
            "first entry is not DefineOntology or Checkpoint",
        )),
    }
}

/// Parse a hex-encoded 32-byte hash string into a Hash.
pub fn parse_hex_hash(hex_str: &str) -> PyResult<Hash> {
    let bytes = hex::decode(hex_str)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid hex hash: {e}")))?;
    if bytes.len() != 32 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "hash must be 32 bytes, got {}",
            bytes.len()
        )));
    }
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&bytes);
    Ok(arr)
}

/// Convert a Python dict of properties to a BTreeMap<String, Value>.
pub fn convert_props(dict: Option<&Bound<'_, PyDict>>) -> PyResult<BTreeMap<String, Value>> {
    let mut map = BTreeMap::new();
    if let Some(d) = dict {
        for (k, v) in d.iter() {
            let key: String = k.extract()?;
            let val = py_to_value(&v)?;
            map.insert(key, val);
        }
    }
    Ok(map)
}

/// Convert a Python object to a Silk Value.
pub fn py_to_value(obj: &Bound<'_, pyo3::PyAny>) -> PyResult<Value> {
    py_to_value_depth(obj, 0)
}

fn py_to_value_depth(obj: &Bound<'_, pyo3::PyAny>, depth: usize) -> PyResult<Value> {
    if depth >= MAX_VALUE_DEPTH {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "value nesting exceeds maximum depth of {MAX_VALUE_DEPTH}"
        )));
    }
    if obj.is_none() {
        Ok(Value::Null)
    } else if let Ok(b) = obj.extract::<bool>() {
        Ok(Value::Bool(b))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(Value::Int(i))
    } else if let Ok(f) = obj.extract::<f64>() {
        Ok(Value::Float(f))
    } else if let Ok(s) = obj.extract::<String>() {
        if s.len() > MAX_STRING_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "string exceeds maximum size of {MAX_STRING_BYTES} bytes (got {})",
                s.len()
            )));
        }
        Ok(Value::String(s))
    } else if let Ok(list) = obj.downcast::<pyo3::types::PyList>() {
        if list.len() > MAX_LIST_ITEMS {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "list exceeds maximum of {MAX_LIST_ITEMS} items (got {})",
                list.len()
            )));
        }
        let items: PyResult<Vec<Value>> = list
            .iter()
            .map(|item| py_to_value_depth(&item, depth + 1))
            .collect();
        Ok(Value::List(items?))
    } else if let Ok(dict) = obj.downcast::<PyDict>() {
        if dict.len() > MAX_MAP_ENTRIES {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "map exceeds maximum of {MAX_MAP_ENTRIES} entries (got {})",
                dict.len()
            )));
        }
        let mut map = BTreeMap::new();
        for (k, v) in dict.iter() {
            let key: String = k.extract()?;
            map.insert(key, py_to_value_depth(&v, depth + 1)?);
        }
        Ok(Value::Map(map))
    } else {
        Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "unsupported value type: {}",
            obj.get_type().name()?
        )))
    }
}

/// Convert a Node to a Python dict.
pub fn node_to_pydict(py: Python<'_>, node: &crate::graph::Node) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("node_id", &node.node_id)?;
    dict.set_item("node_type", &node.node_type)?;
    match &node.subtype {
        Some(st) => dict.set_item("subtype", st)?,
        None => dict.set_item("subtype", py.None())?,
    }
    dict.set_item("label", &node.label)?;
    let props = value_map_to_pydict(py, &node.properties)?;
    dict.set_item("properties", props)?;
    Ok(dict.into())
}

/// Convert an Edge to a Python dict.
pub fn edge_to_pydict(py: Python<'_>, edge: &crate::graph::Edge) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("edge_id", &edge.edge_id)?;
    dict.set_item("edge_type", &edge.edge_type)?;
    dict.set_item("source_id", &edge.source_id)?;
    dict.set_item("target_id", &edge.target_id)?;
    let props = value_map_to_pydict(py, &edge.properties)?;
    dict.set_item("properties", props)?;
    Ok(dict.into())
}

/// Convert a BTreeMap<String, Value> to a Python dict.
pub fn value_map_to_pydict(py: Python<'_>, map: &BTreeMap<String, Value>) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    for (k, v) in map {
        dict.set_item(k, value_to_py(py, v)?)?;
    }
    Ok(dict.into())
}

/// Convert a Silk Value to a Python object.
pub fn value_to_py(py: Python<'_>, val: &Value) -> PyResult<PyObject> {
    value_to_py_depth(py, val, 0)
}

fn value_to_py_depth(py: Python<'_>, val: &Value, depth: usize) -> PyResult<PyObject> {
    if depth >= MAX_VALUE_DEPTH {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "value nesting exceeds maximum depth of {MAX_VALUE_DEPTH}"
        )));
    }
    use pyo3::ToPyObject;
    match val {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => Ok(b.to_object(py)),
        Value::Int(i) => Ok(i.to_object(py)),
        Value::Float(f) => Ok(f.to_object(py)),
        Value::String(s) => Ok(s.to_object(py)),
        Value::List(items) => {
            let py_items: PyResult<Vec<PyObject>> = items
                .iter()
                .map(|v| value_to_py_depth(py, v, depth + 1))
                .collect();
            let list = PyList::new(py, &py_items?)?;
            Ok(list.into())
        }
        Value::Map(m) => {
            let dict = PyDict::new(py);
            for (k, v) in m {
                dict.set_item(k, value_to_py_depth(py, v, depth + 1)?)?;
            }
            Ok(dict.into())
        }
    }
}

/// Convert an Entry to a Python dict.
pub fn entry_to_pydict(py: Python<'_>, entry: &Entry) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("hash", hex::encode(entry.hash))?;
    dict.set_item("author", &entry.author)?;
    dict.set_item("physical_ms", entry.clock.physical_ms)?;
    dict.set_item("logical", entry.clock.logical)?;
    dict.set_item("clock_id", &entry.clock.id)?;
    dict.set_item(
        "next",
        entry.next.iter().map(hex::encode).collect::<Vec<_>>(),
    )?;
    dict.set_item(
        "refs",
        entry.refs.iter().map(hex::encode).collect::<Vec<_>>(),
    )?;

    let payload_json = serde_json::to_string(&entry.payload)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    dict.set_item("payload", payload_json)?;

    Ok(dict.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_hex_hash_valid() {
        let hex_str = "a".repeat(64);
        let hash = parse_hex_hash(&hex_str).unwrap();
        assert_eq!(hash, [0xaa; 32]);
    }

    #[test]
    fn parse_hex_hash_wrong_length() {
        assert!(parse_hex_hash("abcd").is_err());
    }

    #[test]
    fn parse_hex_hash_invalid_chars() {
        let bad = "zz".repeat(32);
        assert!(parse_hex_hash(&bad).is_err());
    }
}
