use criterion::{black_box, criterion_group, criterion_main, Criterion};
use silk::clock::LamportClock;
use silk::entry::{Entry, GraphOp};
use silk::ontology::{NodeTypeDef, Ontology};
use silk::oplog::OpLog;
use std::collections::BTreeMap;

fn make_genesis() -> Entry {
    let ontology = Ontology {
        node_types: BTreeMap::from([(
            "entity".into(),
            NodeTypeDef {
                description: None,
                properties: BTreeMap::new(),
            },
        )]),
        edge_types: BTreeMap::new(),
    };
    Entry::new(
        GraphOp::DefineOntology { ontology },
        vec![],
        vec![],
        LamportClock::new("bench"),
        "bench",
    )
}

fn bench_oplog_append(c: &mut Criterion) {
    c.bench_function("oplog_append_1000_chain", |b| {
        b.iter(|| {
            let genesis = make_genesis();
            let mut oplog = OpLog::new(genesis.clone());
            let mut heads = vec![genesis.hash];
            for i in 0..1000u64 {
                let op = GraphOp::AddNode {
                    node_id: format!("n{i}"),
                    node_type: "entity".into(),
                    label: format!("Node {i}"),
                    properties: BTreeMap::new(),
                };
                let entry = Entry::new(
                    op,
                    heads.clone(),
                    vec![],
                    LamportClock {
                        id: "bench".into(),
                        time: i + 2,
                    },
                    "bench",
                );
                let hash = entry.hash;
                oplog.append(black_box(entry)).unwrap();
                heads = vec![hash];
            }
        })
    });
}

fn bench_oplog_entries_since(c: &mut Criterion) {
    // Pre-build a 10k-entry oplog, then benchmark entries_since.
    let genesis = make_genesis();
    let mut oplog = OpLog::new(genesis.clone());
    let mut heads = vec![genesis.hash];
    let mut mid_hash = genesis.hash;

    for i in 0..10_000u64 {
        let op = GraphOp::AddNode {
            node_id: format!("n{i}"),
            node_type: "entity".into(),
            label: format!("Node {i}"),
            properties: BTreeMap::new(),
        };
        let entry = Entry::new(
            op,
            heads.clone(),
            vec![],
            LamportClock {
                id: "bench".into(),
                time: i + 2,
            },
            "bench",
        );
        let hash = entry.hash;
        oplog.append(entry).unwrap();
        heads = vec![hash];
        if i == 5000 {
            mid_hash = hash;
        }
    }

    c.bench_function("oplog_entries_since_mid_10k", |b| {
        b.iter(|| {
            let delta = oplog.entries_since(Some(black_box(&mid_hash)));
            black_box(delta.len());
        })
    });

    c.bench_function("oplog_entries_since_all_10k", |b| {
        b.iter(|| {
            let all = oplog.entries_since(black_box(None));
            black_box(all.len());
        })
    });
}

criterion_group!(benches, bench_oplog_append, bench_oplog_entries_since);
criterion_main!(benches);
