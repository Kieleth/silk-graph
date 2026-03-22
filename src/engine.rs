use std::collections::{HashMap, HashSet, VecDeque};

use crate::graph::MaterializedGraph;

/// BFS traversal result — node IDs in visit order.
pub fn bfs(
    graph: &MaterializedGraph,
    start: &str,
    max_depth: Option<usize>,
    edge_type_filter: Option<&str>,
) -> Vec<String> {
    let mut visited = HashSet::new();
    let mut result = Vec::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();

    if graph.get_node(start).is_none() {
        return result;
    }

    visited.insert(start.to_string());
    queue.push_back((start.to_string(), 0));

    while let Some((node_id, depth)) = queue.pop_front() {
        result.push(node_id.clone());

        if let Some(max) = max_depth {
            if depth >= max {
                continue;
            }
        }

        let edges = graph.outgoing_edges(&node_id);
        for edge in edges {
            if let Some(filter) = edge_type_filter {
                if edge.edge_type != filter {
                    continue;
                }
            }
            if !visited.contains(&edge.target_id) {
                visited.insert(edge.target_id.clone());
                queue.push_back((edge.target_id.clone(), depth + 1));
            }
        }
    }

    result
}

/// Shortest path between two nodes (unweighted BFS).
/// Returns the path as a list of node IDs (including start and end),
/// or None if no path exists.
pub fn shortest_path(
    graph: &MaterializedGraph,
    start: &str,
    end: &str,
) -> Option<Vec<String>> {
    if graph.get_node(start).is_none() || graph.get_node(end).is_none() {
        return None;
    }
    if start == end {
        return Some(vec![start.to_string()]);
    }

    let mut visited = HashSet::new();
    let mut parent: HashMap<String, String> = HashMap::new();
    let mut queue: VecDeque<String> = VecDeque::new();

    visited.insert(start.to_string());
    queue.push_back(start.to_string());

    while let Some(current) = queue.pop_front() {
        for edge in graph.outgoing_edges(&current) {
            if !visited.contains(&edge.target_id) {
                visited.insert(edge.target_id.clone());
                parent.insert(edge.target_id.clone(), current.clone());
                if edge.target_id == end {
                    // Reconstruct path.
                    let mut path = vec![end.to_string()];
                    let mut cur = end.to_string();
                    while let Some(p) = parent.get(&cur) {
                        path.push(p.clone());
                        cur = p.clone();
                    }
                    path.reverse();
                    return Some(path);
                }
                queue.push_back(edge.target_id.clone());
            }
        }
    }

    None
}

/// Impact analysis: reverse BFS from a node — "what depends on this?"
/// Traverses incoming edges to find all nodes that transitively depend on `node_id`.
pub fn impact_analysis(
    graph: &MaterializedGraph,
    node_id: &str,
    max_depth: Option<usize>,
) -> Vec<String> {
    let mut visited = HashSet::new();
    let mut result = Vec::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();

    if graph.get_node(node_id).is_none() {
        return result;
    }

    visited.insert(node_id.to_string());
    queue.push_back((node_id.to_string(), 0));

    while let Some((current, depth)) = queue.pop_front() {
        result.push(current.clone());

        if let Some(max) = max_depth {
            if depth >= max {
                continue;
            }
        }

        for edge in graph.incoming_edges(&current) {
            if !visited.contains(&edge.source_id) {
                visited.insert(edge.source_id.clone());
                queue.push_back((edge.source_id.clone(), depth + 1));
            }
        }
    }

    result
}

/// Extract subgraph: all nodes and edges within N hops of a start node.
/// Returns (node_ids, edge_ids).
pub fn subgraph(
    graph: &MaterializedGraph,
    start: &str,
    hops: usize,
) -> (Vec<String>, Vec<String>) {
    let mut visited_nodes = HashSet::new();
    let mut visited_edges = HashSet::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();

    if graph.get_node(start).is_none() {
        return (vec![], vec![]);
    }

    visited_nodes.insert(start.to_string());
    queue.push_back((start.to_string(), 0));

    while let Some((node_id, depth)) = queue.pop_front() {
        if depth >= hops {
            continue;
        }

        // Outgoing.
        for edge in graph.outgoing_edges(&node_id) {
            visited_edges.insert(edge.edge_id.clone());
            if !visited_nodes.contains(&edge.target_id) {
                visited_nodes.insert(edge.target_id.clone());
                queue.push_back((edge.target_id.clone(), depth + 1));
            }
        }
        // Incoming.
        for edge in graph.incoming_edges(&node_id) {
            visited_edges.insert(edge.edge_id.clone());
            if !visited_nodes.contains(&edge.source_id) {
                visited_nodes.insert(edge.source_id.clone());
                queue.push_back((edge.source_id.clone(), depth + 1));
            }
        }
    }

    (
        visited_nodes.into_iter().collect(),
        visited_edges.into_iter().collect(),
    )
}

/// Pattern match: find chains matching a sequence of node types connected by edges.
/// E.g., `["signal", "rule", "plan", "action"]` finds all MAPE-K loops.
/// Returns list of chains, each chain being a list of node_ids.
pub fn pattern_match(
    graph: &MaterializedGraph,
    type_sequence: &[&str],
) -> Vec<Vec<String>> {
    if type_sequence.is_empty() {
        return vec![];
    }

    let mut results = Vec::new();

    // Start from all nodes of the first type.
    let start_nodes = graph.nodes_by_type(type_sequence[0]);
    for start in start_nodes {
        let mut chains = vec![vec![start.node_id.clone()]];

        for &next_type in &type_sequence[1..] {
            let mut extended = Vec::new();
            for chain in &chains {
                let last = chain.last().unwrap();
                for edge in graph.outgoing_edges(last) {
                    if let Some(target_node) = graph.get_node(&edge.target_id) {
                        if target_node.node_type == next_type && !chain.contains(&edge.target_id) {
                            let mut new_chain = chain.clone();
                            new_chain.push(edge.target_id.clone());
                            extended.push(new_chain);
                        }
                    }
                }
            }
            chains = extended;
        }

        results.extend(chains);
    }

    results
}

/// Topological sort of nodes connected by directed edges.
/// For DAGs only — returns None if a cycle is detected.
pub fn topological_sort(
    graph: &MaterializedGraph,
) -> Option<Vec<String>> {
    let nodes = graph.all_nodes();
    let node_ids: HashSet<String> = nodes.iter().map(|n| n.node_id.clone()).collect();

    // Compute in-degrees.
    let mut in_degree: HashMap<String, usize> = node_ids.iter().map(|id| (id.clone(), 0)).collect();
    for edge in graph.all_edges() {
        if node_ids.contains(&edge.target_id) && node_ids.contains(&edge.source_id) {
            *in_degree.entry(edge.target_id.clone()).or_default() += 1;
        }
    }

    let mut queue: VecDeque<String> = in_degree
        .iter()
        .filter(|(_, &deg)| deg == 0)
        .map(|(id, _)| id.clone())
        .collect();

    // Sort for determinism.
    let mut sorted: Vec<String> = queue.drain(..).collect();
    sorted.sort();
    queue.extend(sorted);

    let mut result = Vec::new();
    while let Some(node_id) = queue.pop_front() {
        result.push(node_id.clone());
        for edge in graph.outgoing_edges(&node_id) {
            if let Some(deg) = in_degree.get_mut(&edge.target_id) {
                *deg -= 1;
                if *deg == 0 {
                    queue.push_back(edge.target_id.clone());
                }
            }
        }
    }

    if result.len() == node_ids.len() {
        Some(result)
    } else {
        None // Cycle detected.
    }
}

/// Cycle detection: returns true if the graph contains a cycle.
pub fn has_cycle(graph: &MaterializedGraph) -> bool {
    topological_sort(graph).is_none()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::LamportClock;
    use crate::entry::{Entry, GraphOp};
    use crate::ontology::{EdgeTypeDef, NodeTypeDef, Ontology};
    use std::collections::BTreeMap;

    fn test_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([
                ("entity".into(), NodeTypeDef { description: None, properties: BTreeMap::new(), subtypes: None }),
                ("signal".into(), NodeTypeDef { description: None, properties: BTreeMap::new(), subtypes: None }),
                ("rule".into(), NodeTypeDef { description: None, properties: BTreeMap::new(), subtypes: None }),
                ("plan".into(), NodeTypeDef { description: None, properties: BTreeMap::new(), subtypes: None }),
                ("action".into(), NodeTypeDef { description: None, properties: BTreeMap::new(), subtypes: None }),
            ]),
            edge_types: BTreeMap::from([
                ("DEPENDS_ON".into(), EdgeTypeDef { description: None, source_types: vec!["entity".into()], target_types: vec!["entity".into()], properties: BTreeMap::new() }),
                ("TRIGGERS".into(), EdgeTypeDef { description: None, source_types: vec!["signal".into()], target_types: vec!["rule".into()], properties: BTreeMap::new() }),
                ("PRODUCES".into(), EdgeTypeDef { description: None, source_types: vec!["rule".into(), "plan".into(), "action".into()], target_types: vec!["plan".into(), "action".into(), "signal".into()], properties: BTreeMap::new() }),
            ]),
        }
    }

    fn make_entry(op: GraphOp, clock_time: u64) -> Entry {
        Entry::new(op, vec![], vec![], LamportClock { id: "test".into(), time: clock_time }, "test")
    }

    fn add_node(id: &str, ntype: &str, clock: u64) -> Entry {
        make_entry(GraphOp::AddNode { node_id: id.into(), node_type: ntype.into(), label: id.into(), properties: BTreeMap::new(), subtype: None }, clock)
    }

    fn add_edge(id: &str, etype: &str, src: &str, tgt: &str, clock: u64) -> Entry {
        make_entry(GraphOp::AddEdge { edge_id: id.into(), edge_type: etype.into(), source_id: src.into(), target_id: tgt.into(), properties: BTreeMap::new() }, clock)
    }

    /// Build a linear chain: A → B → C → D
    fn linear_graph() -> MaterializedGraph {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&add_node("a", "entity", 1));
        g.apply(&add_node("b", "entity", 2));
        g.apply(&add_node("c", "entity", 3));
        g.apply(&add_node("d", "entity", 4));
        g.apply(&add_edge("ab", "DEPENDS_ON", "a", "b", 5));
        g.apply(&add_edge("bc", "DEPENDS_ON", "b", "c", 6));
        g.apply(&add_edge("cd", "DEPENDS_ON", "c", "d", 7));
        g
    }

    #[test]
    fn bfs_traversal_from_node() {
        let g = linear_graph();
        let visited = bfs(&g, "a", None, None);
        assert_eq!(visited, vec!["a", "b", "c", "d"]);
    }

    #[test]
    fn bfs_respects_depth_limit() {
        let g = linear_graph();
        let visited = bfs(&g, "a", Some(2), None);
        // depth 0: a, depth 1: b, depth 2: c (but c's children not explored)
        assert_eq!(visited, vec!["a", "b", "c"]);
    }

    #[test]
    fn bfs_filters_edge_types() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&add_node("a", "entity", 1));
        g.apply(&add_node("b", "entity", 2));
        g.apply(&add_node("c", "entity", 3));
        g.apply(&add_edge("ab", "DEPENDS_ON", "a", "b", 4));
        g.apply(&add_edge("ac", "DEPENDS_ON", "a", "c", 5));
        // Add a different edge type that should be filtered out.
        // (Using DEPENDS_ON for simplicity — in real ontology this would be different)

        let visited = bfs(&g, "a", None, Some("DEPENDS_ON"));
        assert!(visited.contains(&"a".to_string()));
        assert!(visited.contains(&"b".to_string()));
        assert!(visited.contains(&"c".to_string()));

        // Filter by nonexistent type → only start node.
        let visited2 = bfs(&g, "a", None, Some("NONEXISTENT"));
        assert_eq!(visited2, vec!["a"]);
    }

    #[test]
    fn shortest_path_finds_path() {
        let g = linear_graph();
        let path = shortest_path(&g, "a", "d").unwrap();
        assert_eq!(path, vec!["a", "b", "c", "d"]);
    }

    #[test]
    fn shortest_path_no_path() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&add_node("a", "entity", 1));
        g.apply(&add_node("b", "entity", 2));
        // No edge between them.
        assert!(shortest_path(&g, "a", "b").is_none());
    }

    #[test]
    fn impact_analysis_reverse_traversal() {
        let g = linear_graph(); // a → b → c → d
        // "What depends on d?" → reverse: c, b, a
        let impact = impact_analysis(&g, "d", None);
        assert!(impact.contains(&"d".to_string()));
        assert!(impact.contains(&"c".to_string()));
        assert!(impact.contains(&"b".to_string()));
        assert!(impact.contains(&"a".to_string()));
    }

    #[test]
    fn subgraph_extraction() {
        let g = linear_graph(); // a → b → c → d
        let (nodes, edges) = subgraph(&g, "b", 1);
        // 1 hop from b: a (incoming), c (outgoing)
        assert!(nodes.contains(&"b".to_string()));
        assert!(nodes.contains(&"a".to_string()));
        assert!(nodes.contains(&"c".to_string()));
        assert!(!nodes.contains(&"d".to_string())); // 2 hops away
        assert_eq!(edges.len(), 2); // ab, bc
    }

    #[test]
    fn pattern_match_mape_k_loop() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&add_node("sig1", "signal", 1));
        g.apply(&add_node("rule1", "rule", 2));
        g.apply(&add_node("plan1", "plan", 3));
        g.apply(&add_node("act1", "action", 4));
        g.apply(&add_edge("e1", "TRIGGERS", "sig1", "rule1", 5));
        g.apply(&add_edge("e2", "PRODUCES", "rule1", "plan1", 6));
        g.apply(&add_edge("e3", "PRODUCES", "plan1", "act1", 7));

        let chains = pattern_match(&g, &["signal", "rule", "plan", "action"]);
        assert_eq!(chains.len(), 1);
        assert_eq!(chains[0], vec!["sig1", "rule1", "plan1", "act1"]);
    }

    #[test]
    fn topological_sort_dependency_order() {
        let g = linear_graph(); // a → b → c → d
        let sorted = topological_sort(&g).unwrap();
        // a must come before b, b before c, c before d.
        let pos = |id: &str| sorted.iter().position(|x| x == id).unwrap();
        assert!(pos("a") < pos("b"));
        assert!(pos("b") < pos("c"));
        assert!(pos("c") < pos("d"));
    }

    #[test]
    fn cycle_detection() {
        let mut g = MaterializedGraph::new(test_ontology());
        g.apply(&add_node("a", "entity", 1));
        g.apply(&add_node("b", "entity", 2));
        g.apply(&add_node("c", "entity", 3));
        g.apply(&add_edge("ab", "DEPENDS_ON", "a", "b", 4));
        g.apply(&add_edge("bc", "DEPENDS_ON", "b", "c", 5));
        g.apply(&add_edge("ca", "DEPENDS_ON", "c", "a", 6)); // cycle!

        assert!(has_cycle(&g));
        assert!(topological_sort(&g).is_none());
    }
}
