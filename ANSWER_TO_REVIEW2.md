# Response to Academic Review: The Ontology Question

> Internal working document. Addresses the reviewer's core critique: "Silk calls this 'ontology,' yet academically it is much closer to a typed property-graph schema."

## The Reviewer Is Right

What Silk calls "ontology" is a **typed property-graph schema with validation constraints**. It defines types, connections, required properties, and value constraints. It does not reason, infer, classify, or derive new facts.

The academic definition (Studer et al. 1998): "An ontology is a formal, explicit specification of a shared conceptualization." The key word is *formal* — machine-readable with well-defined logical semantics that enable automated reasoning. Silk's schema is explicit and shared, but not formal in the logic sense. It validates; it does not reason.

The W3C's own position (w3.org/wiki/SchemaVsOntology): "In essence, nothing [distinguishes them]. The term 'schema' is used for simple conceptualisations; 'ontology' for more complex models in more expressive languages like OWL." The distinction is one of degree, not kind.

**Where Silk sits on the spectrum:**

```
Silk is here
    ↓
1. Typed property-graph schema  ← Silk (NodeTypeDef, EdgeTypeDef, constraints)
2. JSON-LD                      ← URI-based identity, no reasoning
3. Schema.org                   ← lightweight vocabulary (~800 types)
4. RDFS                         ← class hierarchies, simple inference (13 rules)
5. SHACL                        ← rich closed-world validation, optional rules
6. OWL 2 EL/QL/RL              ← tractable reasoning profiles
7. OWL 2 DL                    ← full description logic (2NEXPTIME)
8. OWL Full                    ← undecidable
```

The question is: where should Silk go next, and what does each step unlock?

---

## What Silk Has Now

Silk's `Ontology` struct validates:

| Capability | How |
|---|---|
| Node type existence | `validate_node()` rejects unknown types |
| Property types | String, Int, Float, Bool, List, Map, Any — checked at write time |
| Required properties | `required: true` enforced on `add_node` |
| Value constraints | `enum`, `min`/`max`, `min_exclusive`/`max_exclusive`, `min_length`/`max_length`, `pattern` (regex) |
| Edge type existence | `validate_edge()` rejects unknown edge types |
| Edge source/target | `source_types`/`target_types` — **hierarchy-aware** (accepts descendants) |
| Class hierarchy | `parent_type` on NodeTypeDef; `ancestors()`, `descendants()`, `is_subtype_of()` |
| Property inheritance | `effective_properties()` walks ancestor chain, child overrides parent |
| Hierarchy-aware queries | `nodes_by_type("entity")` returns entity + server + all descendants |
| Subtypes (D-024) | One-level specialization with property merging |
| Open properties (D-026) | Unknown properties accepted without validation |
| Monotonic evolution (R-03) | Add types/properties, relax required→optional, never remove |

**What it cannot do:**
- No OWL-style inference — adding facts never derives new facts (class hierarchy is computed from schema, not data)
- No cardinality beyond required/optional — can't say "exactly 2" or "at most 5"
- No cross-property constraints — can't say "if status is 'critical', severity must be present"
- No graph-level validation — can't say "every Server must have at least one RUNS_ON edge"

---

## Step 1: SHACL-Level Constraint Vocabulary ��

> **Status: IMPLEMENTED.** `pattern` (full regex), `min_length`/`max_length`, `min_exclusive`/`max_exclusive` added to `validate_constraints()`. See [FAQ.md](FAQ.md) for constraint reference.

**What it is:** Richer validation constraints within the existing architecture. No new concepts — just more constraint types in `validate_constraints()`.

**What changes in Silk (`src/ontology.rs`):**

| New constraint | SHACL equivalent | Example | Engineering |
|---|---|---|---|
| `pattern` | `sh:pattern` | `"pattern": "^[a-z0-9-]+$"` | Regex match on String values. Add `regex` crate. |
| `min_count` / `max_count` | `sh:minCount` / `sh:maxCount` | `"min_count": 2, "max_count": 5` | Count property occurrences. Requires multi-value property support or List length check. |
| `min_length` / `max_length` | `sh:minLength` / `sh:maxLength` | `"min_length": 1, "max_length": 255` | String length validation. |
| `min_exclusive` | `sh:minExclusive` | `"min_exclusive": 0` | Numeric range (exclusive). |

**What changes in Shelob:** Nothing. Shelob's `shelob.yaml` (LinkML) can declare these constraints and `store.py` passes them through to Silk.

**Value unlocked:**
- Regex validation on hostnames, IPs, slugs (Shelob: `hostname` pattern, `slug` pattern)
- String length limits (Shelob: session messages, deploy logs)
- Exclusive ranges (Shelob: port numbers 1-65535 exclusive of 0)

**Cost:** Small. ~50 LOC in `validate_constraints()`. Add `regex` as optional dependency. Pure additive — no architectural change.

**Does NOT require RDFS. This is independent and should come first.**

---

## Step 2: RDFS-Level Class Hierarchy ✓

> **Status: IMPLEMENTED.** `parent_type` on `NodeTypeDef`. `ancestors()`, `descendants()`, `is_subtype_of()`, `effective_properties()` in `ontology.rs`. `nodes_by_type()` hierarchy-aware in `graph.rs`. Edge validation hierarchy-aware. 17 Rust tests, 12 Python tests.

**What it is:** Declaring that types have parent types, and the system *reasons* about the hierarchy. This is where the word "ontology" starts to earn its name.

### The Concrete Example (Shelob)

Shelob today has 5 coarse types (Entity, Signal, Rule, Plan, Action) with 37 subtypes declared via D-024's flat `subtypes` mechanism. The subtypes merge properties but the system doesn't understand hierarchy.

**Today (flat subtypes, no hierarchy):**
```python
# Shelob's store.py
"entity": {
    "subtypes": {
        "server": { "properties": { "hostname": ... } },
        "project": { "properties": { "slug": ... } },
        "dns_zone": { "properties": { "zone_id": ... } },
    }
}
```

- `query_nodes_by_type("entity")` returns only nodes created as `type="entity"` — NOT servers, NOT projects
- `query_nodes_by_subtype("server")` returns servers, but can't query "all entities including subtypes"
- Edge constraint `source_types: ["entity"]` does NOT accept a `server` node as source
- Shelob works around this by treating subtypes as metadata, not as types in the graph sense

**With RDFS-level hierarchy:**
```python
# server subClassOf entity (declared)
# project subClassOf entity (declared)
# dns_zone subClassOf entity (declared)

query_nodes_by_type("entity")
# → returns ALL entity instances, ALL server instances, ALL project instances, ALL dns_zone instances

# Edge constraint source_types: ["entity"]
# → NOW accepts server, project, dns_zone as valid sources (because they are entities)
```

**The 13 RDFS rules, but only 2 matter here:**
- **rdfs9:** If `Server subClassOf Entity` and `node42 type Server`, then `node42 type Entity` (materialized)
- **rdfs11:** If `WebServer subClassOf Server` and `Server subClassOf Entity`, then `WebServer subClassOf Entity` (transitive)

The other 11 rules deal with domain/range inference and meta-level typing that Silk doesn't need.

### What Changes in Silk

**`src/ontology.rs` — Add hierarchy to `NodeTypeDef`:**
```rust
pub struct NodeTypeDef {
    // ... existing fields ...
    pub parent_type: Option<String>,  // NEW: rdfs:subClassOf
}
```

**`src/ontology.rs` — Compute transitive closure at load/extend time:**
```rust
/// For a given type, return all ancestor types (transitive subClassOf).
pub fn ancestors(&self, node_type: &str) -> Vec<&str> { ... }

/// For a given type, return all descendant types (transitive subClassOf inverse).
pub fn descendants(&self, node_type: &str) -> Vec<&str> { ... }
```

Cost: transitive closure is O(types × depth). For 37 subtypes with depth 2, this is trivial.

**`src/graph.rs` — Make queries hierarchy-aware:**
```rust
pub fn nodes_by_type(&self, node_type: &str) -> Vec<&Node> {
    // OLD: only exact matches
    // NEW: include all descendants of node_type
    let types = self.ontology.descendants(node_type);
    types.iter()
        .flat_map(|t| self.by_type.get(*t))
        .flatten()
        .filter_map(|id| self.get_node(id))
        .collect()
}
```

**`src/graph.rs` — Make edge validation hierarchy-aware:**
```rust
// OLD (graph.rs:152):
if !self.ontology.edge_types.contains_key(edge_type) { quarantine }

// NEW: source/target type check uses ancestors
// "server" is valid for source_types: ["entity"] because server subClassOf entity
fn type_matches(&self, actual_type: &str, allowed_types: &[String]) -> bool {
    allowed_types.iter().any(|allowed| {
        actual_type == allowed || self.ontology.ancestors(actual_type).contains(&allowed.as_str())
    })
}
```

**`src/ontology.rs` — Property inheritance through hierarchy:**
```rust
/// Get all properties for a type, including inherited from ancestors.
pub fn effective_properties(&self, node_type: &str) -> BTreeMap<String, PropertyDef> {
    let mut props = BTreeMap::new();
    // Walk ancestors (most general first), then overlay specific
    for ancestor in self.ancestors(node_type).into_iter().rev() {
        if let Some(def) = self.node_types.get(ancestor) {
            props.extend(def.properties.clone());
        }
    }
    if let Some(def) = self.node_types.get(node_type) {
        props.extend(def.properties.clone());
    }
    props
}
```

### What Changes in Shelob

Shelob's `shelob.yaml` (LinkML) already declares `is_a` relationships — LinkML natively supports class inheritance. The `store.py` currently flattens this into D-024 subtypes.

**Today (`store.py`):** Shelob manually maps LinkML classes to Silk's flat subtypes.

**With RDFS-level Silk:** Shelob passes the hierarchy directly:
```python
ontology = {
    "node_types": {
        "entity": { "properties": { "status": ... } },
        "server": { "parent_type": "entity", "properties": { "hostname": ... } },
        "project": { "parent_type": "entity", "properties": { "slug": ... } },
    }
}
```

Server inherits `status` from Entity. Edge constraints on Entity accept Server. Queries for Entity return servers.

### CRDT Compatibility

**Safe.** RDFS inference on a grow-only schema is monotonic:
- Adding `Server subClassOf Entity` only ADDS inferred type memberships, never removes
- Two peers independently adding different subclass declarations merge by union — both hierarchies are valid
- Silk's monotonic extension model (R-03) already ensures schema only grows
- Transitive closure is a deterministic function of the schema — same schema = same hierarchy on all peers

Research confirms: SU-Set (INRIA 2012) and NextGraph both use CRDTs for RDF graphs. Neither integrates RDFS inference INTO the CRDT — both run inference as a deterministic post-merge step, same as Silk's materialization.

**Nobody has published CRDT-integrated RDFS inference.** Silk doing this would be a genuine contribution.

### Value Unlocked

| Before (flat subtypes) | After (hierarchy) |
|---|---|
| `query_nodes_by_type("entity")` returns 0 results (everything is a subtype) | Returns all servers, projects, dns_zones, etc. |
| Edge `source_types: ["entity"]` rejects server nodes | Accepts servers (server is-a entity) |
| Property `status` must be declared on every subtype | Declared once on Entity, inherited by all |
| Shelob's `store.py` manually flattens LinkML hierarchy | Silk understands hierarchy natively |
| "Find all things in the infrastructure" requires listing every type | `query_nodes_by_type("entity")` just works |

### Cost

Medium. ~200 LOC across `ontology.rs`, `graph.rs`, `python.rs`. Changes query behavior (hierarchy-aware matching). Needs migration path for existing stores (stores without `parent_type` work as before). New tests for inheritance, transitive closure, and CRDT sync of hierarchy changes.

---

## Step 3: SHACL-Level Graph Validation (Post-Materialization Checks)

**What it is:** Validation rules that can see the entire graph, not just individual nodes/edges. This is where you can express "every server must have at least one RUNS_ON edge" or "if a node has status 'critical', it must have a severity property."

### Why This Is Different From Steps 1 and 2

Steps 1 and 2 operate **per-operation**: when you `add_node` or `add_edge`, validation checks that single operation against the schema. SHACL operates **per-graph**: after all operations are applied, validate the entire materialized graph against shapes.

Silk's current validation: "Is this node valid?"
SHACL validation: "Is this graph valid?"

### The Concrete Example (Shelob)

Constraints Shelob can't express today:

```
Every server MUST have at least one RUNS_ON edge (it must run somewhere)
Every project MUST have at least one server (can't exist without infrastructure)
If a server has status "decommissioned", it MUST NOT have active services
Every alert with severity "critical" MUST have an assigned action within the graph
```

These are **graph-level invariants** — they depend on relationships between nodes, not just individual node properties.

### What Changes in Silk

**New: Post-materialization validation layer.** Separate from per-operation ontology validation.

```rust
/// A shape defines validation rules for a node type in graph context.
pub struct Shape {
    pub target_type: String,
    pub constraints: Vec<GraphConstraint>,
}

pub enum GraphConstraint {
    /// Node must have at least N outgoing edges of this type
    MinOutgoing { edge_type: String, count: usize },
    /// Node must have at most N outgoing edges of this type
    MaxOutgoing { edge_type: String, count: usize },
    /// Conditional: if property matches, additional constraints apply
    Conditional {
        property: String,
        value: Value,
        then_constraints: Vec<GraphConstraint>,
    },
}
```

**Python API:**
```python
# Define shapes
shapes = [
    {
        "target_type": "server",
        "constraints": [
            {"min_outgoing": {"edge_type": "RUNS_ON", "count": 1}},
        ]
    },
    {
        "target_type": "alert",
        "constraints": [
            {"conditional": {
                "property": "severity", "value": "critical",
                "then": [{"min_outgoing": {"edge_type": "ASSIGNED_TO", "count": 1}}]
            }}
        ]
    }
]

# Validate the graph against shapes
report = store.validate_shapes(shapes)
# report.conforms → bool
# report.violations → [{"node_id": "srv-1", "constraint": "min_outgoing RUNS_ON", ...}]
```

### Interaction With CRDT / Quarantine

**Critical distinction:** SHACL-style validation does NOT quarantine. It produces a **report**.

Why: Graph-level constraints can be transiently violated during sync. Peer A adds a server, Peer B adds the RUNS_ON edge. After A syncs but before B syncs, A's graph has a server without a RUNS_ON edge — a violation. After B syncs, the violation resolves.

Quarantining the server because it temporarily lacks an edge would be wrong. The correct behavior:

1. Per-operation validation (ontology) → quarantine invalid entries (fast, local, CRDT-safe)
2. Post-materialization validation (shapes) → produce report (graph-level, may be transiently invalid)
3. Application decides what to do with violations (alert, repair, ignore until next sync)

This is exactly how GraphDB implements SHACL: validation runs on commit, violations are reported, data is not rejected.

### Value Unlocked

| Without shapes | With shapes |
|---|---|
| "Server must have RUNS_ON" enforced in application Python code | Enforced by Silk, reported automatically |
| Graph inconsistencies discovered at query time | Discovered at validation time with actionable report |
| No way to express conditional constraints | "If critical, must have action" expressible |
| Shelob's health checks are external scripts | Health invariants are declarative shapes |

### Cost

Medium-large. ~300-400 LOC for the shape validation engine. New `validate_shapes()` method on `PyGraphStore`. New test suite for graph-level validation. Does NOT require RDFS (can work with flat types), but benefits from hierarchy (shapes can target parent types and apply to all descendants).

---

## The Dependency Map

```
Step 1: SHACL constraint vocabulary (regex, cardinality, length)
   ↓ no dependency, do first, immediate value
Step 2: RDFS class hierarchy (subClassOf, property inheritance)
   ↓ independent of Step 1, bigger architectural change
Step 3: SHACL graph-level validation (shapes, post-materialization)
   ↓ benefits from Step 2 (shapes target parent types)
   ↓ benefits from Step 1 (richer per-property constraints in shapes)
```

Steps 1 and 2 are independent and can be parallel. Step 3 benefits from both but doesn't strictly require them.

---

## What This Means for the Whitepaper

If Silk implements Steps 1-3, the terminology becomes defensible:

- **After Step 1:** "Schema with rich validation constraints" — still not an ontology
- **After Step 2:** "Schema with class hierarchy and inference" — RDFS-level, academically defensible as a lightweight ontology
- **After Step 3:** "Schema with class hierarchy, inference, and graph-level validation" — RDFS + SHACL level, stronger than most industry "ontologies" (Neo4j, TigerGraph, etc.)

The reviewer scored the ontology model 7/10. Steps 1-3 would move it to ~8.5/10 on that scale. Full OWL would be 10/10 but is architecturally incompatible with Silk's closed-world, CRDT-convergent design (OWL's open-world assumption conflicts with validation-at-write-time).

**The honest framing for the whitepaper:** "Silk implements a typed property-graph schema with RDFS-level class hierarchy and SHACL-inspired validation. It does not perform OWL-style open-world reasoning. The schema is the structural contract for a distributed CRDT graph — it ensures well-formedness across replicas, not semantic completeness."

---

## References

- Gruber (1993) — "A Translation Approach to Portable Ontology Specifications"
- Studer, Benjamins, Fensel (1998) — "Knowledge Engineering: Principles and Methods"
- Guarino (1998) — "Formal Ontology and Information Systems" (FOIS'98)
- W3C RDF 1.1 Semantics — RDFS entailment rules (13 rules)
- W3C SHACL Specification — Shapes Constraint Language
- W3C OWL 2 Profiles — EL, QL, RL tractable subsets
- Corman, Reutter, Savkovic (ISWC 2018) — "Semantics and Validation of Recursive SHACL"
- Inferray (VLDB 2016) — "Fast In-Memory RDF Inference" (21.3M triples/sec for RDFS closure)
- SU-Set (INRIA 2012) — "Commutative Replicated Data Type for Semantic Stores"
- Knublauch (TopQuadrant) — "Why I Don't Use OWL Anymore"
- DuCharme — "You Probably Don't Need OWL"
- Apache Jena RDFS Reasoner documentation
- GraphDB SHACL Validation documentation
- RDFox incremental materialization documentation
