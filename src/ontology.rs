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
    /// Extensible constraints — validated at write time.
    /// Built-in: "enum" (list of allowed values), "min"/"max" (numeric range).
    /// Community contributions welcome for additional constraint types.
    #[serde(default)]
    pub constraints: Option<BTreeMap<String, serde_json::Value>>,
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
    /// RDFS-level class hierarchy (Step 2). If set, this type is a subclass
    /// of `parent_type`. Queries for the parent type include this type.
    /// Edge constraints accepting the parent type also accept this type.
    /// Properties are inherited from the parent (child overrides on conflict).
    #[serde(default)]
    pub parent_type: Option<String>,
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
    /// A property value violates a constraint (enum, range, etc.)
    ConstraintViolation {
        type_name: String,
        property: String,
        constraint: String,
        message: String,
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
            ValidationError::ConstraintViolation {
                type_name,
                property,
                constraint,
                message,
            } => write!(
                f,
                "'{type_name}'.'{property}' violates constraint '{constraint}': {message}"
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
    // -- RDFS-level class hierarchy (Step 2) --

    /// Return all ancestor types of `node_type` (transitive parent_type chain).
    /// Does not include `node_type` itself. Returns empty vec if no parent.
    pub fn ancestors(&self, node_type: &str) -> Vec<&str> {
        let mut result = Vec::new();
        let mut current = node_type;
        // Guard against cycles (max 100 levels — no real ontology is deeper)
        for _ in 0..100 {
            match self
                .node_types
                .get(current)
                .and_then(|d| d.parent_type.as_deref())
            {
                Some(parent) => {
                    result.push(parent);
                    current = parent;
                }
                None => break,
            }
        }
        result
    }

    /// Return all descendant types of `node_type` (types whose ancestor chain includes it).
    /// Does not include `node_type` itself.
    pub fn descendants(&self, node_type: &str) -> Vec<&str> {
        // Collect all types that have node_type anywhere in their ancestor chain.
        self.node_types
            .iter()
            .filter(|(name, _)| {
                name.as_str() != node_type && self.ancestors(name).contains(&node_type)
            })
            .map(|(name, _)| name.as_str())
            .collect()
    }

    /// Check if `child_type` is the same as or a descendant of `parent_type`.
    pub fn is_subtype_of(&self, child_type: &str, parent_type: &str) -> bool {
        child_type == parent_type || self.ancestors(child_type).contains(&parent_type)
    }

    /// Get all properties for a type, including those inherited from ancestors.
    /// Ancestors' properties are applied first (most general), then overridden
    /// by more specific types. Same order as Python MRO: parent first, child overrides.
    pub fn effective_properties(&self, node_type: &str) -> BTreeMap<String, PropertyDef> {
        let mut chain: Vec<&str> = self.ancestors(node_type);
        chain.reverse(); // most general first
        chain.push(node_type);

        let mut props = BTreeMap::new();
        for t in chain {
            if let Some(def) = self.node_types.get(t) {
                for (k, v) in &def.properties {
                    props.insert(k.clone(), v.clone());
                }
            }
        }
        props
    }

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

        // Step 2: use effective_properties (includes inherited from ancestors)
        let base_props = self.effective_properties(node_type);

        match (&def.subtypes, subtype) {
            // Type has subtypes and caller provided one
            (Some(subtypes), Some(st)) => {
                match subtypes.get(st) {
                    Some(st_def) => {
                        // Known subtype — merge inherited + type-level + subtype-level
                        let mut merged = base_props;
                        merged.extend(st_def.properties.clone());
                        validate_properties(node_type, &merged, properties)
                    }
                    None => {
                        // D-026: unknown subtype — validate inherited + type-level only
                        validate_properties(node_type, &base_props, properties)
                    }
                }
            }
            // Type has subtypes but caller didn't provide one — error
            (Some(subtypes), None) => Err(ValidationError::MissingSubtype {
                node_type: node_type.to_string(),
                allowed: subtypes.keys().cloned().collect(),
            }),
            // D-026: accept subtypes even if type doesn't declare any
            (None, Some(_st)) => validate_properties(node_type, &base_props, properties),
            // Type has no subtypes and caller didn't provide one — validate as before
            (None, None) => validate_properties(node_type, &base_props, properties),
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

        // Hierarchy-aware: accept if actual type IS one of the allowed types
        // OR is a descendant of any allowed type (RDFS rdfs9).
        if !def
            .source_types
            .iter()
            .any(|t| self.is_subtype_of(source_node_type, t))
        {
            return Err(ValidationError::InvalidSource {
                edge_type: edge_type.to_string(),
                node_type: source_node_type.to_string(),
                allowed: def.source_types.clone(),
            });
        }

        if !def
            .target_types
            .iter()
            .any(|t| self.is_subtype_of(target_node_type, t))
        {
            return Err(ValidationError::InvalidTarget {
                edge_type: edge_type.to_string(),
                node_type: target_node_type.to_string(),
                allowed: def.target_types.clone(),
            });
        }

        validate_properties(edge_type, &def.properties, properties)
    }

    /// Validate a single property update against the ontology.
    /// Checks that the value type matches the property definition.
    /// Unknown properties are accepted (D-026: ontology defines minimum, not maximum).
    pub fn validate_property_update(
        &self,
        node_type: &str,
        subtype: Option<&str>,
        key: &str,
        value: &Value,
    ) -> Result<(), ValidationError> {
        let def = match self.node_types.get(node_type) {
            Some(d) => d,
            None => return Ok(()), // Unknown node type — can't validate
        };

        // Merge type-level + subtype-level property definitions
        let mut merged = def.properties.clone();
        if let (Some(subtypes), Some(st)) = (&def.subtypes, subtype) {
            if let Some(st_def) = subtypes.get(st) {
                merged.extend(st_def.properties.clone());
            }
        }

        // D-026: unknown properties accepted without validation
        let prop_def = match merged.get(key) {
            Some(d) => d,
            None => return Ok(()),
        };

        // Type check
        if prop_def.value_type != ValueType::Any && !value_matches_type(value, &prop_def.value_type)
        {
            return Err(ValidationError::WrongPropertyType {
                type_name: node_type.to_string(),
                property: key.to_string(),
                expected: prop_def.value_type.clone(),
                got: value_type_name(value).to_string(),
            });
        }

        // Constraint check
        if let Some(constraints) = &prop_def.constraints {
            validate_constraints(node_type, key, value, constraints)?;
        }

        Ok(())
    }

    /// Validate that the ontology itself is internally consistent.
    /// All source_types/target_types in edge defs must reference existing node types.
    pub fn validate_self(&self) -> Result<(), ValidationError> {
        // Validate edge source/target references
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
        // Validate parent_type references (Step 2: class hierarchy)
        for (type_name, type_def) in &self.node_types {
            if let Some(ref parent) = type_def.parent_type {
                if !self.node_types.contains_key(parent) {
                    return Err(ValidationError::UnknownNodeType(format!(
                        "{}: parent_type '{}' does not exist",
                        type_name, parent
                    )));
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

        // Validate constraints (if any)
        if let Some(constraints) = &prop_def.constraints {
            validate_constraints(type_name, prop_name, value, constraints)?;
        }
    }

    Ok(())
}

/// Validate a property value against its constraints.
/// Built-in constraints: "enum" (allowed values), "min"/"max" (numeric range).
/// Unknown constraint names are silently ignored — enables forward compatibility
/// with community-contributed constraint types.
fn validate_constraints(
    type_name: &str,
    prop_name: &str,
    value: &Value,
    constraints: &BTreeMap<String, serde_json::Value>,
) -> Result<(), ValidationError> {
    // "enum": list of allowed string values
    if let Some(serde_json::Value::Array(allowed)) = constraints.get("enum") {
        if let Value::String(s) = value {
            let allowed_strs: Vec<&str> = allowed.iter().filter_map(|v| v.as_str()).collect();
            if !allowed_strs.contains(&s.as_str()) {
                return constraint_err(
                    type_name,
                    prop_name,
                    "enum",
                    format!("value '{}' not in allowed set {:?}", s, allowed_strs),
                );
            }
        }
    }

    // Numeric bounds (4 variants share the same extract-compare pattern)
    check_numeric_bound(
        type_name,
        prop_name,
        value,
        constraints,
        "min",
        |n, b| n < b,
        |n, b| format!("value {} is less than minimum {}", n, b),
    )?;
    check_numeric_bound(
        type_name,
        prop_name,
        value,
        constraints,
        "max",
        |n, b| n > b,
        |n, b| format!("value {} exceeds maximum {}", n, b),
    )?;
    check_numeric_bound(
        type_name,
        prop_name,
        value,
        constraints,
        "min_exclusive",
        |n, b| n <= b,
        |n, b| format!("value {} must be greater than {}", n, b),
    )?;
    check_numeric_bound(
        type_name,
        prop_name,
        value,
        constraints,
        "max_exclusive",
        |n, b| n >= b,
        |n, b| format!("value {} must be less than {}", n, b),
    )?;

    // String length bounds
    check_string_length(
        type_name,
        prop_name,
        value,
        constraints,
        "min_length",
        |len, bound| len < bound,
        |len, bound| format!("string length {} is less than minimum {}", len, bound),
    )?;
    check_string_length(
        type_name,
        prop_name,
        value,
        constraints,
        "max_length",
        |len, bound| len > bound,
        |len, bound| format!("string length {} exceeds maximum {}", len, bound),
    )?;

    // "pattern": regex match on string values
    if let Some(serde_json::Value::String(pattern)) = constraints.get("pattern") {
        if let Value::String(s) = value {
            match regex::Regex::new(pattern) {
                Ok(re) if !re.is_match(s) => {
                    return constraint_err(
                        type_name,
                        prop_name,
                        "pattern",
                        format!("value '{}' does not match pattern '{}'", s, pattern),
                    );
                }
                Err(e) => {
                    return constraint_err(
                        type_name,
                        prop_name,
                        "pattern",
                        format!("invalid regex pattern '{}': {}", pattern, e),
                    );
                }
                _ => {}
            }
        }
    }

    // Unknown constraint names are silently ignored (forward compat).
    Ok(())
}

/// Helper: extract numeric value from a Value.
fn value_as_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Int(n) => Some(*n as f64),
        Value::Float(n) => Some(*n),
        _ => None,
    }
}

/// Helper: check a numeric bound constraint.
fn check_numeric_bound(
    type_name: &str,
    prop_name: &str,
    value: &Value,
    constraints: &BTreeMap<String, serde_json::Value>,
    key: &str,
    violates: impl Fn(f64, f64) -> bool,
    msg: impl Fn(f64, f64) -> String,
) -> Result<(), ValidationError> {
    if let Some(bound_val) = constraints.get(key) {
        if let Some(bound) = bound_val.as_f64() {
            if let Some(n) = value_as_f64(value) {
                if violates(n, bound) {
                    return constraint_err(type_name, prop_name, key, msg(n, bound));
                }
            }
        }
    }
    Ok(())
}

/// Helper: check a string length constraint.
fn check_string_length(
    type_name: &str,
    prop_name: &str,
    value: &Value,
    constraints: &BTreeMap<String, serde_json::Value>,
    key: &str,
    violates: impl Fn(u64, u64) -> bool,
    msg: impl Fn(u64, u64) -> String,
) -> Result<(), ValidationError> {
    if let Some(serde_json::Value::Number(n)) = constraints.get(key) {
        if let (Some(bound), Value::String(s)) = (n.as_u64(), value) {
            if violates(s.len() as u64, bound) {
                return constraint_err(type_name, prop_name, key, msg(s.len() as u64, bound));
            }
        }
    }
    Ok(())
}

/// Helper: construct a ConstraintViolation error.
fn constraint_err(
    type_name: &str,
    prop_name: &str,
    constraint: &str,
    message: String,
) -> Result<(), ValidationError> {
    Err(ValidationError::ConstraintViolation {
        type_name: type_name.to_string(),
        property: prop_name.to_string(),
        constraint: constraint.to_string(),
        message,
    })
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
                                constraints: None,
                            },
                        )]),
                        subtypes: None,
                        parent_type: None,
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
                                    constraints: None,
                                },
                            ),
                            (
                                "port".into(),
                                PropertyDef {
                                    value_type: ValueType::Int,
                                    required: false,
                                    description: None,
                                    constraints: None,
                                },
                            ),
                        ]),
                        subtypes: None,
                        parent_type: None,
                    },
                ),
                (
                    "rule".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                        parent_type: None,
                    },
                ),
                (
                    "action".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                        parent_type: None,
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
                    parent_type: None,
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
                    parent_type: None,
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

    // --- New constraint tests (Step 1: SHACL-inspired vocabulary) ---

    fn constrained_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([(
                "item".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::from([
                        (
                            "slug".into(),
                            PropertyDef {
                                value_type: ValueType::String,
                                required: false,
                                description: None,
                                constraints: Some(BTreeMap::from([
                                    (
                                        "pattern".to_string(),
                                        serde_json::Value::String("^[a-z0-9-]+$".to_string()),
                                    ),
                                    (
                                        "min_length".to_string(),
                                        serde_json::Value::Number(1.into()),
                                    ),
                                    (
                                        "max_length".to_string(),
                                        serde_json::Value::Number(63.into()),
                                    ),
                                ])),
                            },
                        ),
                        (
                            "score".into(),
                            PropertyDef {
                                value_type: ValueType::Float,
                                required: false,
                                description: None,
                                constraints: Some(BTreeMap::from([
                                    ("min_exclusive".to_string(), serde_json::json!(0.0)),
                                    ("max_exclusive".to_string(), serde_json::json!(100.0)),
                                ])),
                            },
                        ),
                    ]),
                    subtypes: None,
                    parent_type: None,
                },
            )]),
            edge_types: BTreeMap::new(),
        }
    }

    #[test]
    fn pattern_valid_slug() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("slug".into(), Value::String("my-project-1".into()))]);
        assert!(ont.validate_node("item", None, &props).is_ok());
    }

    #[test]
    fn pattern_rejects_uppercase() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("slug".into(), Value::String("My-Project".into()))]);
        assert!(ont.validate_node("item", None, &props).is_err());
    }

    #[test]
    fn pattern_rejects_spaces() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("slug".into(), Value::String("has space".into()))]);
        assert!(ont.validate_node("item", None, &props).is_err());
    }

    #[test]
    fn min_length_accepts_valid() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("slug".into(), Value::String("a".into()))]);
        assert!(ont.validate_node("item", None, &props).is_ok());
    }

    #[test]
    fn min_length_rejects_empty() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("slug".into(), Value::String("".into()))]);
        let err = ont.validate_node("item", None, &props).unwrap_err();
        assert!(
            matches!(err, ValidationError::ConstraintViolation { constraint, .. } if constraint == "min_length")
        );
    }

    #[test]
    fn max_length_rejects_too_long() {
        let ont = constrained_ontology();
        let long = "a".repeat(64);
        let props = BTreeMap::from([("slug".into(), Value::String(long))]);
        let err = ont.validate_node("item", None, &props).unwrap_err();
        assert!(
            matches!(err, ValidationError::ConstraintViolation { constraint, .. } if constraint == "max_length")
        );
    }

    #[test]
    fn max_length_accepts_boundary() {
        let ont = constrained_ontology();
        let exact = "a".repeat(63);
        let props = BTreeMap::from([("slug".into(), Value::String(exact))]);
        assert!(ont.validate_node("item", None, &props).is_ok());
    }

    #[test]
    fn min_exclusive_rejects_boundary() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("score".into(), Value::Float(0.0))]);
        let err = ont.validate_node("item", None, &props).unwrap_err();
        assert!(
            matches!(err, ValidationError::ConstraintViolation { constraint, .. } if constraint == "min_exclusive")
        );
    }

    #[test]
    fn min_exclusive_accepts_above() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("score".into(), Value::Float(0.001))]);
        assert!(ont.validate_node("item", None, &props).is_ok());
    }

    #[test]
    fn max_exclusive_rejects_boundary() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("score".into(), Value::Float(100.0))]);
        let err = ont.validate_node("item", None, &props).unwrap_err();
        assert!(
            matches!(err, ValidationError::ConstraintViolation { constraint, .. } if constraint == "max_exclusive")
        );
    }

    #[test]
    fn max_exclusive_accepts_below() {
        let ont = constrained_ontology();
        let props = BTreeMap::from([("score".into(), Value::Float(99.999))]);
        assert!(ont.validate_node("item", None, &props).is_ok());
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

    // --- Step 2: RDFS class hierarchy tests ---

    fn hierarchy_ontology() -> Ontology {
        // thing → entity → server (two levels)
        //       → event
        Ontology {
            node_types: BTreeMap::from([
                (
                    "thing".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::from([(
                            "name".into(),
                            PropertyDef {
                                value_type: ValueType::String,
                                required: true,
                                description: None,
                                constraints: None,
                            },
                        )]),
                        subtypes: None,
                        parent_type: None, // root
                    },
                ),
                (
                    "entity".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::from([(
                            "status".into(),
                            PropertyDef {
                                value_type: ValueType::String,
                                required: false,
                                description: None,
                                constraints: None,
                            },
                        )]),
                        subtypes: None,
                        parent_type: Some("thing".into()), // entity extends thing
                    },
                ),
                (
                    "server".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::from([(
                            "ip".into(),
                            PropertyDef {
                                value_type: ValueType::String,
                                required: false,
                                description: None,
                                constraints: None,
                            },
                        )]),
                        subtypes: None,
                        parent_type: Some("entity".into()), // server extends entity
                    },
                ),
                (
                    "event".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                        parent_type: Some("thing".into()), // event extends thing
                    },
                ),
            ]),
            edge_types: BTreeMap::from([(
                "RELATES_TO".into(),
                EdgeTypeDef {
                    description: None,
                    source_types: vec!["thing".into()], // accepts any thing descendant
                    target_types: vec!["entity".into()], // accepts entity or server
                    properties: BTreeMap::new(),
                },
            )]),
        }
    }

    #[test]
    fn ancestors_empty_for_root() {
        let ont = hierarchy_ontology();
        assert!(ont.ancestors("thing").is_empty());
    }

    #[test]
    fn ancestors_single_parent() {
        let ont = hierarchy_ontology();
        assert_eq!(ont.ancestors("entity"), vec!["thing"]);
    }

    #[test]
    fn ancestors_transitive() {
        let ont = hierarchy_ontology();
        // server → entity → thing
        assert_eq!(ont.ancestors("server"), vec!["entity", "thing"]);
    }

    #[test]
    fn descendants_of_root() {
        let ont = hierarchy_ontology();
        let mut desc = ont.descendants("thing");
        desc.sort();
        assert_eq!(desc, vec!["entity", "event", "server"]);
    }

    #[test]
    fn descendants_of_entity() {
        let ont = hierarchy_ontology();
        assert_eq!(ont.descendants("entity"), vec!["server"]);
    }

    #[test]
    fn descendants_of_leaf() {
        let ont = hierarchy_ontology();
        assert!(ont.descendants("server").is_empty());
    }

    #[test]
    fn is_subtype_of_self() {
        let ont = hierarchy_ontology();
        assert!(ont.is_subtype_of("server", "server"));
    }

    #[test]
    fn is_subtype_of_parent() {
        let ont = hierarchy_ontology();
        assert!(ont.is_subtype_of("server", "entity"));
        assert!(ont.is_subtype_of("server", "thing"));
    }

    #[test]
    fn is_not_subtype_of_sibling() {
        let ont = hierarchy_ontology();
        assert!(!ont.is_subtype_of("server", "event"));
    }

    #[test]
    fn effective_properties_inherits() {
        let ont = hierarchy_ontology();
        let props = ont.effective_properties("server");
        // server should have: name (from thing), status (from entity), ip (own)
        assert!(props.contains_key("name"));
        assert!(props.contains_key("status"));
        assert!(props.contains_key("ip"));
    }

    #[test]
    fn effective_properties_root_has_own_only() {
        let ont = hierarchy_ontology();
        let props = ont.effective_properties("thing");
        assert!(props.contains_key("name"));
        assert!(!props.contains_key("status"));
    }

    #[test]
    fn validate_node_inherits_required_from_ancestor() {
        let ont = hierarchy_ontology();
        // server requires "name" (inherited from thing)
        let err = ont.validate_node("server", None, &BTreeMap::new());
        assert!(err.is_err());

        let props = BTreeMap::from([("name".into(), Value::String("web-01".into()))]);
        assert!(ont.validate_node("server", None, &props).is_ok());
    }

    #[test]
    fn validate_edge_hierarchy_aware() {
        let ont = hierarchy_ontology();
        // RELATES_TO: source=thing, target=entity
        // server is-a thing, server is-a entity → both should pass
        let empty = BTreeMap::new();
        assert!(ont
            .validate_edge("RELATES_TO", "server", "server", &empty)
            .is_ok());
        assert!(ont
            .validate_edge("RELATES_TO", "event", "entity", &empty)
            .is_ok());
        assert!(ont
            .validate_edge("RELATES_TO", "thing", "entity", &empty)
            .is_ok());
    }

    #[test]
    fn validate_edge_hierarchy_rejects_wrong_branch() {
        let ont = hierarchy_ontology();
        // RELATES_TO target must be entity or descendant. event is not entity's descendant.
        let empty = BTreeMap::new();
        assert!(ont
            .validate_edge("RELATES_TO", "thing", "event", &empty)
            .is_err());
    }

    #[test]
    fn validate_self_rejects_dangling_parent() {
        let ont = Ontology {
            node_types: BTreeMap::from([(
                "orphan".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                    parent_type: Some("ghost".into()), // doesn't exist
                },
            )]),
            edge_types: BTreeMap::new(),
        };
        assert!(ont.validate_self().is_err());
    }
}
