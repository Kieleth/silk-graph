use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use silk::sync::{entries_missing, merge_entries};
use silk::{
    EdgeTypeDef, Entry, GraphOp, LamportClock, NodeTypeDef, Ontology, OpLog, SyncOffer, SyncPayload,
};
use std::collections::BTreeMap;

fn make_ontology() -> Ontology {
    Ontology {
        node_types: BTreeMap::from([(
            "entity".into(),
            NodeTypeDef {
                description: None,
                properties: BTreeMap::new(),
                subtypes: None,
            },
        )]),
        edge_types: BTreeMap::from([(
            "LINKS".into(),
            EdgeTypeDef {
                description: None,
                source_types: vec!["entity".into()],
                target_types: vec!["entity".into()],
                properties: BTreeMap::new(),
            },
        )]),
    }
}

fn make_oplog_with_nodes(n: usize, author: &str) -> OpLog {
    let genesis = Entry::new(
        GraphOp::DefineOntology {
            ontology: make_ontology(),
        },
        vec![],
        vec![],
        LamportClock::new(author),
        author,
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
                id: author.into(),
                time: (i + 1) as u64,
            },
            author,
        );
        let hash = entry.hash;
        oplog.append(entry).unwrap();
        heads = vec![hash];
    }
    oplog
}

/// Measures the cost of generating a SyncOffer (bloom filter + heads) from an
/// oplog of increasing size. This is the first step of every sync round, so
/// its performance bounds the sync initiation rate.
fn sync_offer_generation(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_offer_generation");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [100, 1_000, 10_000] {
        let oplog = make_oplog_with_nodes(n, "peer-a");
        group.bench_with_input(BenchmarkId::new("N", n), &oplog, |b, oplog| {
            b.iter(|| black_box(SyncOffer::from_oplog(oplog, 42)))
        });
    }
    group.finish();
}

/// Measures full sync cost when two peers have zero overlap (except genesis).
/// Peer A has N nodes, peer B has 0. This is the worst-case scenario — a
/// full bootstrap via delta sync rather than snapshot.
fn sync_full_transfer(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_full_transfer");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [100, 1_000] {
        let oplog_a = make_oplog_with_nodes(n, "peer-a");
        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: make_ontology(),
            },
            vec![],
            vec![],
            LamportClock::new("peer-a"),
            "peer-a",
        );

        group.bench_with_input(BenchmarkId::new("N", n), &n, |b, _| {
            b.iter(|| {
                let mut oplog_b = OpLog::new(genesis.clone());
                let offer_b = SyncOffer::from_oplog(&oplog_b, 0);
                let payload = entries_missing(&oplog_a, &offer_b);
                let merged = merge_entries(&mut oplog_b, &payload.entries).unwrap();
                black_box(merged);
            })
        });
    }
    group.finish();
}

/// Measures incremental sync cost when peers share 90% of their data.
/// Peer A has 1000 nodes, peer B has 900 of the same entries.
/// This is the common steady-state scenario — small deltas after initial sync.
fn sync_incremental(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_incremental");
    group.measurement_time(std::time::Duration::from_secs(5));

    let author = "peer-a";
    let genesis = Entry::new(
        GraphOp::DefineOntology {
            ontology: make_ontology(),
        },
        vec![],
        vec![],
        LamportClock::new(author),
        author,
    );

    // Build the full 1000-node oplog and collect entries for the partial copy.
    let mut oplog_a = OpLog::new(genesis.clone());
    let mut heads = vec![genesis.hash];
    let mut all_entries = Vec::new();
    for i in 0..1000usize {
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
                id: author.into(),
                time: (i + 1) as u64,
            },
            author,
        );
        let hash = entry.hash;
        all_entries.push(entry.clone());
        oplog_a.append(entry).unwrap();
        heads = vec![hash];
    }

    // Peer B has the first 900 entries.
    let mut oplog_b = OpLog::new(genesis.clone());
    for entry in &all_entries[..900] {
        oplog_b.append(entry.clone()).unwrap();
    }

    group.bench_function("900_of_1000_shared", |b| {
        b.iter(|| {
            let offer_b = SyncOffer::from_oplog(&oplog_b, 900);
            let payload = entries_missing(&oplog_a, &offer_b);
            black_box(payload.entries.len());
        })
    });
    group.finish();
}

/// Measures convergence cost after a network partition heals. Two peers
/// diverge from the same genesis, each writing 500 unique nodes. Then
/// they sync bidirectionally. This tests the bloom filter's ability to
/// efficiently identify the symmetric difference.
fn sync_partition_heal(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_partition_heal");
    group.measurement_time(std::time::Duration::from_secs(5));

    let genesis = Entry::new(
        GraphOp::DefineOntology {
            ontology: make_ontology(),
        },
        vec![],
        vec![],
        LamportClock::new("peer-a"),
        "peer-a",
    );

    // Peer A: genesis + 500 unique nodes.
    let mut oplog_a = OpLog::new(genesis.clone());
    let mut heads_a = vec![genesis.hash];
    for i in 0..500usize {
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: format!("a{i}"),
                node_type: "entity".into(),
                subtype: None,
                label: format!("A-{i}"),
                properties: BTreeMap::new(),
            },
            heads_a.clone(),
            vec![],
            LamportClock {
                id: "peer-a".into(),
                time: (i + 1) as u64,
            },
            "peer-a",
        );
        let hash = entry.hash;
        oplog_a.append(entry).unwrap();
        heads_a = vec![hash];
    }

    // Peer B: genesis + 500 different unique nodes.
    let mut oplog_b = OpLog::new(genesis.clone());
    let mut heads_b = vec![genesis.hash];
    for i in 0..500usize {
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: format!("b{i}"),
                node_type: "entity".into(),
                subtype: None,
                label: format!("B-{i}"),
                properties: BTreeMap::new(),
            },
            heads_b.clone(),
            vec![],
            LamportClock {
                id: "peer-b".into(),
                time: (i + 1) as u64,
            },
            "peer-b",
        );
        let hash = entry.hash;
        oplog_b.append(entry).unwrap();
        heads_b = vec![hash];
    }

    group.bench_function("500_each_diverged", |b| {
        b.iter(|| {
            // A → B direction.
            let offer_b = SyncOffer::from_oplog(&oplog_b, 500);
            let payload_a_to_b = entries_missing(&oplog_a, &offer_b);

            // B → A direction.
            let offer_a = SyncOffer::from_oplog(&oplog_a, 500);
            let payload_b_to_a = entries_missing(&oplog_b, &offer_a);

            black_box((payload_a_to_b.entries.len(), payload_b_to_a.entries.len()));
        })
    });
    group.finish();
}

/// Measures SyncPayload serialization and deserialization throughput.
/// The payload is the wire format for delta sync — its (de)serialization
/// cost directly impacts sync latency.
fn sync_payload_serialization(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_payload_serialization");
    group.measurement_time(std::time::Duration::from_secs(5));

    for n in [100, 1_000] {
        let oplog = make_oplog_with_nodes(n, "peer-a");
        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: make_ontology(),
            },
            vec![],
            vec![],
            LamportClock::new("peer-a"),
            "peer-a",
        );
        let empty = OpLog::new(genesis);
        let offer = SyncOffer::from_oplog(&empty, 0);
        let payload = entries_missing(&oplog, &offer);

        let bytes = payload.to_bytes();

        group.bench_with_input(BenchmarkId::new("serialize", n), &payload, |b, payload| {
            b.iter(|| black_box(payload.to_bytes()))
        });

        group.bench_with_input(BenchmarkId::new("deserialize", n), &bytes, |b, bytes| {
            b.iter(|| black_box(SyncPayload::from_bytes(bytes).unwrap()))
        });
    }
    group.finish();
}

/// Measures sync cost with varying overlap percentages between two peers.
/// Peer A has 1,000 nodes. Peer B shares X% of A's entries and has its own
/// unique entries to fill to 1,000 total. Tests: 1%, 10%, 50%, 90% overlap.
/// This reveals how the bloom filter performs across the overlap spectrum.
fn sync_divergence(c: &mut Criterion) {
    let mut group = c.benchmark_group("sync_divergence");
    group.measurement_time(std::time::Duration::from_secs(5));

    let n = 1_000usize;
    let author_a = "peer-a";
    let author_b = "peer-b";

    // Build peer A's full oplog and collect entries.
    let genesis = Entry::new(
        GraphOp::DefineOntology {
            ontology: make_ontology(),
        },
        vec![],
        vec![],
        LamportClock::new(author_a),
        author_a,
    );
    let mut oplog_a = OpLog::new(genesis.clone());
    let mut heads_a = vec![genesis.hash];
    let mut entries_a = Vec::new();
    for i in 0..n {
        let entry = Entry::new(
            GraphOp::AddNode {
                node_id: format!("a{i}"),
                node_type: "entity".into(),
                subtype: None,
                label: format!("A-{i}"),
                properties: BTreeMap::new(),
            },
            heads_a.clone(),
            vec![],
            LamportClock {
                id: author_a.into(),
                time: (i + 1) as u64,
            },
            author_a,
        );
        let hash = entry.hash;
        entries_a.push(entry.clone());
        oplog_a.append(entry).unwrap();
        heads_a = vec![hash];
    }

    for overlap_pct in [1u32, 10, 50, 90] {
        let shared = (n as u32 * overlap_pct / 100) as usize;
        let unique_b = n - shared;

        // Build peer B: shared entries from A + unique entries from B.
        let mut oplog_b = OpLog::new(genesis.clone());
        for entry in &entries_a[..shared] {
            oplog_b.append(entry.clone()).unwrap();
        }
        let mut heads_b = if shared > 0 {
            vec![entries_a[shared - 1].hash]
        } else {
            vec![genesis.hash]
        };
        for i in 0..unique_b {
            let entry = Entry::new(
                GraphOp::AddNode {
                    node_id: format!("b{i}"),
                    node_type: "entity".into(),
                    subtype: None,
                    label: format!("B-{i}"),
                    properties: BTreeMap::new(),
                },
                heads_b.clone(),
                vec![],
                LamportClock {
                    id: author_b.into(),
                    time: (shared + i + 1) as u64,
                },
                author_b,
            );
            let hash = entry.hash;
            oplog_b.append(entry).unwrap();
            heads_b = vec![hash];
        }

        group.bench_with_input(
            BenchmarkId::new("overlap_pct", overlap_pct),
            &overlap_pct,
            |b, _| {
                b.iter(|| {
                    // A → B: what does A have that B doesn't?
                    let offer_b = SyncOffer::from_oplog(&oplog_b, (shared + unique_b) as u64);
                    let payload_a_to_b = entries_missing(&oplog_a, &offer_b);

                    // B → A: what does B have that A doesn't?
                    let offer_a = SyncOffer::from_oplog(&oplog_a, n as u64);
                    let payload_b_to_a = entries_missing(&oplog_b, &offer_a);

                    black_box((payload_a_to_b.entries.len(), payload_b_to_a.entries.len()));
                })
            },
        );
    }
    group.finish();
}

criterion_group!(
    benches,
    sync_offer_generation,
    sync_full_transfer,
    sync_incremental,
    sync_partition_heal,
    sync_payload_serialization,
    sync_divergence,
);
criterion_main!(benches);
