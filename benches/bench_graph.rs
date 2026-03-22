use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use silk::{
    EdgeTypeDef, Entry, GraphOp, LamportClock, MaterializedGraph, NodeTypeDef, Ontology, OpLog,
};
use std::collections::BTreeMap;

fn lcg_next(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *state
}

fn make_ontology() -> Ontology {
    Ontology {
        node_types: BTreeMap::from([
            (
                "entity".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            ),
            (
                "signal".into(),
                NodeTypeDef {
                    description: None,
                    properties: BTreeMap::new(),
                    subtypes: None,
                },
            ),
        ]),
        edge_types: BTreeMap::from([(
            "LINKS".into(),
            EdgeTypeDef {
                description: None,
                source_types: vec!["entity".into(), "signal".into()],
                target_types: vec!["entity".into(), "signal".into()],
                properties: BTreeMap::new(),
            },
        )]),
    }
}

/// Build a MaterializedGraph with `n_nodes` nodes and `n_edges` random edges.
/// Uses a seeded LCG for deterministic results across runs.
fn build_graph(n_nodes: usize, n_edges: usize) -> MaterializedGraph {
    let mut graph = MaterializedGraph::new(make_ontology());
    let mut rng_state: u64 = 12345;

    // Add nodes — alternate between "entity" and "signal" types for pattern_match benchmarks.
    for i in 0..n_nodes {
        let node_type = if i % 2 == 0 { "entity" } else { "signal" };
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: format!("n{i}"),
                node_type: node_type.into(),
                subtype: None,
                label: format!("Node {i}"),
                properties: BTreeMap::new(),
            },
            vec![],
            vec![],
            LamportClock {
                id: "bench".into(),
                time: (i + 1) as u64,
            },
            "bench",
        );
        graph.apply(&entry);
    }

    // Add edges with deterministic random source/target.
    for i in 0..n_edges {
        let src = (lcg_next(&mut rng_state) as usize) % n_nodes;
        let mut tgt = (lcg_next(&mut rng_state) as usize) % n_nodes;
        if tgt == src {
            tgt = (src + 1) % n_nodes;
        }
        let entry = Entry::new(
            GraphOp::AddEdge {
                edge_id: format!("e{i}"),
                edge_type: "LINKS".into(),
                source_id: format!("n{src}"),
                target_id: format!("n{tgt}"),
                properties: BTreeMap::new(),
            },
            vec![],
            vec![],
            LamportClock {
                id: "bench".into(),
                time: (n_nodes + i + 1) as u64,
            },
            "bench",
        );
        graph.apply(&entry);
    }

    graph
}

/// Measures sustained AddNode throughput through OpLog + MaterializedGraph.
/// This is the write path hot loop — every graph mutation goes through it.
fn add_node_throughput(c: &mut Criterion) {
    let mut group = c.benchmark_group("add_node_throughput");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [100, 1_000, 10_000] {
        let ontology = make_ontology();
        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: ontology.clone(),
            },
            vec![],
            vec![],
            LamportClock::new("bench"),
            "bench",
        );

        group.bench_with_input(BenchmarkId::new("N", n), &n, |b, &n| {
            b.iter(|| {
                let mut oplog = OpLog::new(genesis.clone());
                let mut graph = MaterializedGraph::new(ontology.clone());
                let mut heads = vec![genesis.hash];
                for i in 0..n {
                    let entry = Entry::new(
                        GraphOp::AddNode {
                            node_id: format!("n{i}"),
                            node_type: "entity".into(),
                            subtype: None,
                            label: format!("Node {i}"),
                            properties: BTreeMap::new(),
                        },
                        heads.clone(),
                        vec![],
                        LamportClock {
                            id: "bench".into(),
                            time: (i + 1) as u64,
                        },
                        "bench",
                    );
                    let hash = entry.hash;
                    graph.apply(&entry);
                    oplog.append(entry).unwrap();
                    heads = vec![hash];
                }
                black_box(oplog.len());
            })
        });
    }
    group.finish();
}

/// Measures the cost of rebuilding a MaterializedGraph from an OpLog of N entries.
/// This happens on startup (cold cache) or when a new peer bootstraps from a snapshot.
fn materialization_from_scratch(c: &mut Criterion) {
    let mut group = c.benchmark_group("materialization_from_scratch");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [100, 1_000, 10_000] {
        let ontology = make_ontology();
        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: ontology.clone(),
            },
            vec![],
            vec![],
            LamportClock::new("bench"),
            "bench",
        );
        let mut oplog = OpLog::new(genesis.clone());
        let mut heads = vec![genesis.hash];
        for i in 0..n {
            let entry = Entry::new(
                GraphOp::AddNode {
                    node_id: format!("n{i}"),
                    node_type: "entity".into(),
                    subtype: None,
                    label: format!("Node {i}"),
                    properties: BTreeMap::new(),
                },
                heads.clone(),
                vec![],
                LamportClock {
                    id: "bench".into(),
                    time: (i + 1) as u64,
                },
                "bench",
            );
            let hash = entry.hash;
            oplog.append(entry).unwrap();
            heads = vec![hash];
        }

        let entries = oplog.entries_since(None);

        group.bench_with_input(BenchmarkId::new("N", n), &entries, |b, entries| {
            b.iter(|| {
                let mut graph = MaterializedGraph::new(ontology.clone());
                graph.apply_all(entries);
                black_box(graph.all_nodes().len());
            })
        });
    }
    group.finish();
}

/// Measures BFS traversal cost on graphs with ~3 edges per node.
/// BFS is the foundation for shortest_path, impact_analysis, and subgraph
/// extraction — its performance bounds all reachability queries.
fn bfs_traversal(c: &mut Criterion) {
    let mut group = c.benchmark_group("bfs_traversal");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [1_000, 10_000] {
        let graph = build_graph(n, n * 3);
        group.bench_with_input(BenchmarkId::new("N", n), &graph, |b, graph| {
            b.iter(|| {
                let result = silk::engine::bfs(graph, "n0", None, None);
                black_box(result.len());
            })
        });
    }
    group.finish();
}

/// Measures shortest_path cost between node 0 and node N/2 on random graphs.
/// This models the "how are these two things related?" query pattern that
/// operators use for root-cause analysis.
fn shortest_path_bench(c: &mut Criterion) {
    let mut group = c.benchmark_group("shortest_path");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [1_000, 10_000] {
        let graph = build_graph(n, n * 3);
        let target = format!("n{}", n / 2);
        group.bench_with_input(BenchmarkId::new("N", n), &graph, |b, graph| {
            b.iter(|| {
                let result = silk::engine::shortest_path(graph, "n0", &target);
                black_box(result);
            })
        });
    }
    group.finish();
}

/// Measures reverse BFS (impact analysis) from a central node.
/// Impact analysis answers "what depends on this node?" — critical for
/// understanding blast radius during incidents.
fn impact_analysis_bench(c: &mut Criterion) {
    let mut group = c.benchmark_group("impact_analysis");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [1_000, 10_000] {
        let graph = build_graph(n, n * 3);
        let central = format!("n{}", n / 2);
        group.bench_with_input(BenchmarkId::new("N", n), &graph, |b, graph| {
            b.iter(|| {
                let result = silk::engine::impact_analysis(graph, &central, None);
                black_box(result.len());
            })
        });
    }
    group.finish();
}

/// Measures pattern_match with a 2-type sequence ("entity" -> "signal").
/// Pattern matching is how Silk finds structural motifs (e.g., MAPE-K loops)
/// in the knowledge graph.
fn pattern_match_bench(c: &mut Criterion) {
    let mut group = c.benchmark_group("pattern_match");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [1_000, 10_000] {
        let graph = build_graph(n, n * 3);
        group.bench_with_input(BenchmarkId::new("N", n), &graph, |b, graph| {
            b.iter(|| {
                let result = silk::engine::pattern_match(graph, &["entity", "signal"]);
                black_box(result.len());
            })
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    add_node_throughput,
    materialization_from_scratch,
    bfs_traversal,
    shortest_path_bench,
    impact_analysis_bench,
    pattern_match_bench,
);
criterion_main!(benches);
