use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

use crate::entry::Value;

/// The type of a property value.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValueType {
    String,
    Int,
    Float,
    Bool,
    List,
    Map,
    /// Accept any Value variant.
    Any,
}

/// Definition of a single property on a node or edge type.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PropertyDef {
    pub value_type: ValueType,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub description: Option<String>,
}

/// Definition of a subtype within a node type (D-024).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SubtypeDef {
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub properties: BTreeMap<String, PropertyDef>,
}

/// Definition of a node type in the ontology.
///
/// If `subtypes` is `Some`, then `add_node` requires a `subtype` parameter
/// and properties are validated against the subtype's definition.
/// If `subtypes` is `None`, the type works as before (D-024 backward compat).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeTypeDef {
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub properties: BTreeMap<String, PropertyDef>,
    /// Optional subtype definitions. When present, `add_node` must specify
    /// a subtype and properties are validated per-subtype (D-024).
    #[serde(default)]
    pub subtypes: Option<BTreeMap<String, SubtypeDef>>,
}

/// Definition of an edge type in the ontology.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EdgeTypeDef {
    #[serde(default)]
    pub description: Option<String>,
    /// Which node types can be the source of this edge.
    pub source_types: Vec<String>,
    /// Which node types can be the target of this edge.
    pub target_types: Vec<String>,
    #[serde(default)]
    pub properties: BTreeMap<String, PropertyDef>,
}

/// Immutable ontology — the vocabulary and rules of a Silk graph.
///
/// Defined once at genesis, locked forever. Every operation is validated
/// against this ontology before being appended to the DAG.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Ontology {
    pub node_types: BTreeMap<String, NodeTypeDef>,
    pub edge_types: BTreeMap<String, EdgeTypeDef>,
}

/// Validation errors returned when an operation violates the ontology.
#[derive(Debug, Clone, PartialEq)]
pub enum ValidationError {
    UnknownNodeType(String),
    UnknownEdgeType(String),
    InvalidSource {
        edge_type: String,
        node_type: String,
        allowed: Vec<String>,
    },
    InvalidTarget {
        edge_type: String,
        node_type: String,
        allowed: Vec<String>,
    },
    MissingRequiredProperty {
        type_name: String,
        property: String,
    },
    WrongPropertyType {
        type_name: String,
        property: String,
        expected: ValueType,
        got: String,
    },
    UnknownProperty {
        type_name: String,
        property: String,
    },
    MissingSubtype {
        node_type: String,
        allowed: Vec<String>,
    },
    UnknownSubtype {
        node_type: String,
        subtype: String,
        allowed: Vec<String>,
    },
    UnexpectedSubtype {
        node_type: String,
        subtype: String,
    },
}

impl std::fmt::Display for ValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ValidationError::UnknownNodeType(t) => write!(f, "unknown node type: '{t}'"),
            ValidationError::UnknownEdgeType(t) => write!(f, "unknown edge type: '{t}'"),
            ValidationError::InvalidSource {
                edge_type,
                node_type,
                allowed,
            } => write!(
                f,
                "edge '{edge_type}' cannot have source type '{node_type}' (allowed: {allowed:?})"
            ),
            ValidationError::InvalidTarget {
                edge_type,
                node_type,
                allowed,
            } => write!(
                f,
                "edge '{edge_type}' cannot have target type '{node_type}' (allowed: {allowed:?})"
            ),
            ValidationError::MissingRequiredProperty {
                type_name,
                property,
            } => write!(f, "'{type_name}' requires property '{property}'"),
            ValidationError::WrongPropertyType {
                type_name,
                property,
                expected,
                got,
            } => write!(
                f,
                "'{type_name}'.'{property}' expects {expected:?}, got {got}"
            ),
            ValidationError::UnknownProperty {
                type_name,
                property,
            } => write!(f, "'{type_name}' has no property '{property}' in ontology"),
            ValidationError::MissingSubtype { node_type, allowed } => {
                write!(f, "'{node_type}' requires a subtype (allowed: {allowed:?})")
            }
            ValidationError::UnknownSubtype {
                node_type,
                subtype,
                allowed,
            } => write!(
                f,
                "'{node_type}' has no subtype '{subtype}' (allowed: {allowed:?})"
            ),
            ValidationError::UnexpectedSubtype { node_type, subtype } => write!(
                f,
                "'{node_type}' does not define subtypes, but got subtype '{subtype}'"
            ),
        }
    }
}

/// An additive ontology extension — monotonic evolution only (R-03).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OntologyExtension {
    /// New node types to add.
    #[serde(default)]
    pub node_types: BTreeMap<String, NodeTypeDef>,
    /// New edge types to add.
    #[serde(default)]
    pub edge_types: BTreeMap<String, EdgeTypeDef>,
    /// Updates to existing node types (add properties, subtypes, relax required).
    #[serde(default)]
    pub node_type_updates: BTreeMap<String, NodeTypeUpdate>,
}

/// Additive update to an existing node type.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeTypeUpdate {
    /// New optional properties to add.
    #[serde(default)]
    pub add_properties: BTreeMap<String, PropertyDef>,
    /// Properties to relax from required to optional.
    #[serde(default)]
    pub relax_properties: Vec<String>,
    /// New subtypes to add.
    #[serde(default)]
    pub add_subtypes: BTreeMap<String, SubtypeDef>,
}

/// Errors from monotonic ontology extension (R-03).
#[derive(Debug, Clone, PartialEq)]
pub enum MonotonicityError {
    DuplicateNodeType(String),
    DuplicateEdgeType(String),
    UnknownNodeType(String),
    DuplicateProperty {
        type_name: String,
        property: String,
    },
    UnknownProperty {
        type_name: String,
        property: String,
    },
    /// Wraps a ValidationError from validate_self() after merge.
    ValidationFailed(ValidationError),
}

impl std::fmt::Display for MonotonicityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MonotonicityError::DuplicateNodeType(t) => {
                write!(f, "node type '{t}' already exists")
            }
            MonotonicityError::DuplicateEdgeType(t) => {
                write!(f, "edge type '{t}' already exists")
            }
            MonotonicityError::UnknownNodeType(t) => {
                write!(f, "cannot update unknown node type '{t}'")
            }
            MonotonicityError::DuplicateProperty {
                type_name,
                property,
            } => {
                write!(f, "property '{property}' already exists on '{type_name}'")
            }
            MonotonicityError::UnknownProperty {
                type_name,
                property,
            } => {
                write!(
                    f,
                    "property '{property}' does not exist on '{type_name}' (cannot relax)"
                )
            }
            MonotonicityError::ValidationFailed(e) => {
                write!(f, "ontology validation failed after merge: {e}")
            }
        }
    }
}

impl Ontology {
    /// Validate that a node type exists and its properties conform.
    ///
    /// If the type defines subtypes (D-024), `subtype` must be `Some` and
    /// properties are validated against the subtype's definition.
    /// If the type does not define subtypes, `subtype` must be `None`.
    pub fn validate_node(
        &self,
        node_type: &str,
        subtype: Option<&str>,
        properties: &BTreeMap<String, Value>,
    ) -> Result<(), ValidationError> {
        let def = self
            .node_types
            .get(node_type)
            .ok_or_else(|| ValidationError::UnknownNodeType(node_type.to_string()))?;

        match (&def.subtypes, subtype) {
            // Type has subtypes and caller provided one
            (Some(subtypes), Some(st)) => {
                match subtypes.get(st) {
                    Some(st_def) => {
                        // Known subtype — merge type-level + subtype-level properties
                        let mut merged = def.properties.clone();
                        merged.extend(st_def.properties.clone());
                        validate_properties(node_type, &merged, properties)
                    }
                    None => {
                        // D-026: unknown subtype — validate type-level properties only
                        validate_properties(node_type, &def.properties, properties)
                    }
                }
            }
            // Type has subtypes but caller didn't provide one — error
            (Some(subtypes), None) => Err(ValidationError::MissingSubtype {
                node_type: node_type.to_string(),
                allowed: subtypes.keys().cloned().collect(),
            }),
            // D-026: accept subtypes even if type doesn't declare any
            (None, Some(_st)) => validate_properties(node_type, &def.properties, properties),
            // Type has no subtypes and caller didn't provide one — validate as before
            (None, None) => validate_properties(node_type, &def.properties, properties),
        }
    }

    /// Validate that an edge type exists, source/target types are allowed,
    /// and properties conform.
    pub fn validate_edge(
        &self,
        edge_type: &str,
        source_node_type: &str,
        target_node_type: &str,
        properties: &BTreeMap<String, Value>,
    ) -> Result<(), ValidationError> {
        let def = self
            .edge_types
            .get(edge_type)
            .ok_or_else(|| ValidationError::UnknownEdgeType(edge_type.to_string()))?;

        if !def.source_types.iter().any(|t| t == source_node_type) {
            return Err(ValidationError::InvalidSource {
                edge_type: edge_type.to_string(),
                node_type: source_node_type.to_string(),
                allowed: def.source_types.clone(),
            });
        }

        if !def.target_types.iter().any(|t| t == target_node_type) {
            return Err(ValidationError::InvalidTarget {
                edge_type: edge_type.to_string(),
                node_type: target_node_type.to_string(),
                allowed: def.target_types.clone(),
            });
        }

        validate_properties(edge_type, &def.properties, properties)
    }

    /// Validate that the ontology itself is internally consistent.
    /// All source_types/target_types in edge defs must reference existing node types.
    pub fn validate_self(&self) -> Result<(), ValidationError> {
        for (edge_name, edge_def) in &self.edge_types {
            for src in &edge_def.source_types {
                if !self.node_types.contains_key(src) {
                    return Err(ValidationError::InvalidSource {
                        edge_type: edge_name.clone(),
                        node_type: src.clone(),
                        allowed: self.node_types.keys().cloned().collect(),
                    });
                }
            }
            for tgt in &edge_def.target_types {
                if !self.node_types.contains_key(tgt) {
                    return Err(ValidationError::InvalidTarget {
                        edge_type: edge_name.clone(),
                        node_type: tgt.clone(),
                        allowed: self.node_types.keys().cloned().collect(),
                    });
                }
            }
        }
        Ok(())
    }

    /// R-03: Merge an additive extension into this ontology.
    /// Only monotonic (additive) changes are allowed:
    /// - New node types (must not already exist)
    /// - New edge types (must not already exist)
    /// - Updates to existing node types: add properties, relax required→optional, add subtypes
    pub fn merge_extension(&mut self, ext: &OntologyExtension) -> Result<(), MonotonicityError> {
        // Validate: new node types don't already exist
        for name in ext.node_types.keys() {
            if self.node_types.contains_key(name) {
                return Err(MonotonicityError::DuplicateNodeType(name.clone()));
            }
        }

        // Validate: new edge types don't already exist
        for name in ext.edge_types.keys() {
            if self.edge_types.contains_key(name) {
                return Err(MonotonicityError::DuplicateEdgeType(name.clone()));
            }
        }

        // Validate node_type_updates reference existing types
        for (type_name, update) in &ext.node_type_updates {
            let def = self
                .node_types
                .get(type_name)
                .ok_or_else(|| MonotonicityError::UnknownNodeType(type_name.clone()))?;

            // Validate: add_properties don't already exist
            for prop_name in update.add_properties.keys() {
                if def.properties.contains_key(prop_name) {
                    return Err(MonotonicityError::DuplicateProperty {
                        type_name: type_name.clone(),
                        property: prop_name.clone(),
                    });
                }
            }

            // Validate: relax_properties exist and are currently required
            for prop_name in &update.relax_properties {
                match def.properties.get(prop_name) {
                    Some(prop_def) if prop_def.required => {} // ok
                    Some(_) => {} // already optional — idempotent, allow it
                    None => {
                        return Err(MonotonicityError::UnknownProperty {
                            type_name: type_name.clone(),
                            property: prop_name.clone(),
                        });
                    }
                }
            }

            // Validate: add_subtypes don't already exist (if subtypes are defined)
            if !update.add_subtypes.is_empty() {
                if let Some(ref existing) = def.subtypes {
                    for st_name in update.add_subtypes.keys() {
                        if existing.contains_key(st_name) {
                            return Err(MonotonicityError::DuplicateProperty {
                                type_name: type_name.clone(),
                                property: format!("subtype:{st_name}"),
                            });
                        }
                    }
                }
            }
        }

        // Apply: extend node_types
        self.node_types.extend(ext.node_types.clone());

        // Apply: extend edge_types
        self.edge_types.extend(ext.edge_types.clone());

        // Apply: update existing node types
        for (type_name, update) in &ext.node_type_updates {
            let def = self.node_types.get_mut(type_name).unwrap(); // validated above

            // Add new properties
            def.properties.extend(update.add_properties.clone());

            // Relax required → optional
            for prop_name in &update.relax_properties {
                if let Some(prop_def) = def.properties.get_mut(prop_name) {
                    prop_def.required = false;
                }
            }

            // Add subtypes
            if !update.add_subtypes.is_empty() {
                let subtypes = def.subtypes.get_or_insert_with(BTreeMap::new);
                subtypes.extend(update.add_subtypes.clone());
            }
        }

        // Validate the merged ontology is internally consistent
        self.validate_self()
            .map_err(MonotonicityError::ValidationFailed)?;

        Ok(())
    }
}

/// Validate properties against their definitions.
fn validate_properties(
    type_name: &str,
    defs: &BTreeMap<String, PropertyDef>,
    values: &BTreeMap<String, Value>,
) -> Result<(), ValidationError> {
    // Check required properties are present
    for (prop_name, prop_def) in defs {
        if prop_def.required && !values.contains_key(prop_name) {
            return Err(ValidationError::MissingRequiredProperty {
                type_name: type_name.to_string(),
                property: prop_name.clone(),
            });
        }
    }

    // Check all provided properties are known and correctly typed
    for (prop_name, value) in values {
        // D-026: accept unknown properties without validation.
        // The ontology defines the minimum, not the maximum.
        let prop_def = match defs.get(prop_name) {
            Some(def) => def,
            None => continue,
        };

        if prop_def.value_type != ValueType::Any {
            let actual_type = value_type_name(value);
            let expected = &prop_def.value_type;
            if !value_matches_type(value, expected) {
                return Err(ValidationError::WrongPropertyType {
                    type_name: type_name.to_string(),
                    property: prop_name.clone(),
                    expected: expected.clone(),
                    got: actual_type.to_string(),
                });
            }
        }
    }

    Ok(())
}

fn value_matches_type(value: &Value, expected: &ValueType) -> bool {
    matches!(
        (value, expected),
        (Value::Null, _)
            | (Value::String(_), ValueType::String)
            | (Value::Int(_), ValueType::Int)
            | (Value::Float(_), ValueType::Float)
            | (Value::Bool(_), ValueType::Bool)
            | (Value::List(_), ValueType::List)
            | (Value::Map(_), ValueType::Map)
            | (_, ValueType::Any)
    )
}

fn value_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::Float(_) => "float",
        Value::String(_) => "string",
        Value::List(_) => "list",
        Value::Map(_) => "map",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn devops_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([
                (
                    "signal".into(),
                    NodeTypeDef {
                        description: Some("Something observed".into()),
                        properties: BTreeMap::from([(
                            "severity".into(),
                            PropertyDef {
                                value_type: ValueType::String,
                                required: true,
                                description: None,
                            },
                        )]),
                        subtypes: None,
                    },
                ),
                (
                    "entity".into(),
                    NodeTypeDef {
                        description: Some("Something that exists".into()),
                        properties: BTreeMap::from([
                            (
                                "status".into(),
                                PropertyDef {
                                    value_type: ValueType::String,
                                    required: false,
                                    description: None,
                                },
                            ),
                            (
                                "port".into(),
                                PropertyDef {
                                    value_type: ValueType::Int,
                                    required: false,
                                    description: None,
                                },
                            ),
                        ]),
                        subtypes: None,
                    },
                ),
                (
                    "rule".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                    },
                ),
                (
                    "action".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                    },
                ),
            ]),
            edge_types: BTreeMap::from([
                (
                    "OBSERVES".into(),
                    EdgeTypeDef {
                        description: None,
                        source_types: vec!["signal".into()],
                        target_types: vec!["entity".into()],
                        properties: BTreeMap::new(),
                    },
                ),
                (
                    "TRIGGERS".into(),
                    EdgeTypeDef {
                        description: None,
                        source_types: vec!["signal".into()],
                        target_types: vec!["rule".into()],
                        properties: BTreeMap::new(),
                    },
                ),
                (
                    "RUNS_ON".into(),
                    EdgeTypeDef {
                        description: None,
                        source_types: vec!["entity".into()],
                        target_types: vec!["entity".into()],
                        properties: BTreeMap::new(),
                    },
                ),
            ]),
        }
    }

    // --- Node validation ---

    #[test]
    fn validate_node_valid() {
        let ont = devops_ontology();
        let props = BTreeMap::from([("severity".into(), Value::String("critical".into()))]);
        assert!(ont.validate_node("signal", None, &props).is_ok());
    }

    #[test]
    fn validate_node_unknown_type() {
        let ont = devops_ontology();
        let err = ont
            .validate_node("potato", None, &BTreeMap::new())
            .unwrap_err();
        assert!(matches!(err, ValidationError::UnknownNodeType(t) if t == "potato"));
    }

    #[test]
    fn validate_node_missing_required() {
        let ont = devops_ontology();
        let err = ont
            .validate_node("signal", None, &BTreeMap::new())
            .unwrap_err();
        assert!(
            matches!(err, ValidationError::MissingRequiredProperty { property, .. } if property == "severity")
        );
    }

    #[test]
    fn validate_node_wrong_type() {
        let ont = devops_ontology();
        let props = BTreeMap::from([("severity".into(), Value::Int(5))]);
        let err = ont.validate_node("signal", None, &props).unwrap_err();
        assert!(
            matches!(err, ValidationError::WrongPropertyType { property, .. } if property == "severity")
        );
    }

    #[test]
    fn validate_node_unknown_property_accepted() {
        // D-026: unknown properties are accepted without validation
        let ont = devops_ontology();
        let props = BTreeMap::from([
            ("severity".into(), Value::String("warn".into())),
            ("bogus".into(), Value::Bool(true)),
        ]);
        assert!(ont.validate_node("signal", None, &props).is_ok());
    }

    #[test]
    fn validate_node_optional_property_absent() {
        let ont = devops_ontology();
        // entity has optional "status" — omitting it is fine
        assert!(ont.validate_node("entity", None, &BTreeMap::new()).is_ok());
    }

    #[test]
    fn validate_node_null_accepted_for_any_type() {
        let ont = devops_ontology();
        // Null is accepted for any typed property (represents absence)
        let props = BTreeMap::from([("severity".into(), Value::Null)]);
        assert!(ont.validate_node("signal", None, &props).is_ok());
    }

    // --- Edge validation ---

    #[test]
    fn validate_edge_valid() {
        let ont = devops_ontology();
        assert!(ont
            .validate_edge("OBSERVES", "signal", "entity", &BTreeMap::new())
            .is_ok());
    }

    #[test]
    fn validate_edge_unknown_type() {
        let ont = devops_ontology();
        let err = ont
            .validate_edge("FLIES_TO", "signal", "entity", &BTreeMap::new())
            .unwrap_err();
        assert!(matches!(err, ValidationError::UnknownEdgeType(t) if t == "FLIES_TO"));
    }

    #[test]
    fn validate_edge_invalid_source() {
        let ont = devops_ontology();
        // OBSERVES requires source=signal, not entity
        let err = ont
            .validate_edge("OBSERVES", "entity", "entity", &BTreeMap::new())
            .unwrap_err();
        assert!(matches!(err, ValidationError::InvalidSource { .. }));
    }

    #[test]
    fn validate_edge_invalid_target() {
        let ont = devops_ontology();
        // OBSERVES requires target=entity, not signal
        let err = ont
            .validate_edge("OBSERVES", "signal", "signal", &BTreeMap::new())
            .unwrap_err();
        assert!(matches!(err, ValidationError::InvalidTarget { .. }));
    }

    // --- Self-validation ---

    #[test]
    fn validate_self_consistent() {
        let ont = devops_ontology();
        assert!(ont.validate_self().is_ok());
    }

    #[test]
    fn validate_self_dangling_source() {
        let ont = Ontology {
            node_types: BTreeMap::from([(
                "entity".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            )]),
            edge_types: BTreeMap::from([(
                "OBSERVES".into(),
                EdgeTypeDef {
                    description: None,
                    source_types: vec!["ghost".into()], // doesn't exist
                    target_types: vec!["entity".into()],
                    properties: BTreeMap::new(),
                },
            )]),
        };
        let err = ont.validate_self().unwrap_err();
        assert!(
            matches!(err, ValidationError::InvalidSource { node_type, .. } if node_type == "ghost")
        );
    }

    #[test]
    fn validate_self_dangling_target() {
        let ont = Ontology {
            node_types: BTreeMap::from([(
                "signal".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            )]),
            edge_types: BTreeMap::from([(
                "OBSERVES".into(),
                EdgeTypeDef {
                    description: None,
                    source_types: vec!["signal".into()],
                    target_types: vec!["phantom".into()], // doesn't exist
                    properties: BTreeMap::new(),
                },
            )]),
        };
        let err = ont.validate_self().unwrap_err();
        assert!(
            matches!(err, ValidationError::InvalidTarget { node_type, .. } if node_type == "phantom")
        );
    }

    // --- Serialization ---

    #[test]
    fn ontology_roundtrip_msgpack() {
        let ont = devops_ontology();
        let bytes = rmp_serde::to_vec(&ont).unwrap();
        let decoded: Ontology = rmp_serde::from_slice(&bytes).unwrap();
        assert_eq!(ont, decoded);
    }

    #[test]
    fn ontology_roundtrip_json() {
        let ont = devops_ontology();
        let json = serde_json::to_string(&ont).unwrap();
        let decoded: Ontology = serde_json::from_str(&json).unwrap();
        assert_eq!(ont, decoded);
    }
}
