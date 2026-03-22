use criterion::{black_box, criterion_group, criterion_main, Criterion};
use silk::entry::{Entry, GraphOp, Value};
use silk::clock::LamportClock;
use std::collections::BTreeMap;

fn bench_entry_creation(c: &mut Criterion) {
    c.bench_function("entry_create_add_node", |b| {
        b.iter(|| {
            let op = GraphOp::AddNode {
                node_id: black_box("server-1".into()),
                node_type: black_box("entity".into()),
                subtype: None,
                label: black_box("Production Server".into()),
                properties: BTreeMap::from([
                    ("ip".into(), Value::String("10.0.0.1".into())),
                    ("port".into(), Value::Int(8080)),
                ]),
            };
            let clock = LamportClock { id: "inst-a".into(), time: 1 };
            Entry::new(op, vec![], vec![], clock, "inst-a")
        })
    });
}

fn bench_entry_roundtrip(c: &mut Criterion) {
    let op = GraphOp::AddNode {
        node_id: "server-1".into(),
        node_type: "entity".into(),
        subtype: None,
        label: "Production Server".into(),
        properties: BTreeMap::from([
            ("ip".into(), Value::String("10.0.0.1".into())),
            ("port".into(), Value::Int(8080)),
        ]),
    };
    let clock = LamportClock { id: "inst-a".into(), time: 1 };
    let entry = Entry::new(op, vec![], vec![], clock, "inst-a");
    let bytes = entry.to_bytes();

    c.bench_function("entry_serialize", |b| {
        b.iter(|| black_box(entry.to_bytes()))
    });

    c.bench_function("entry_deserialize", |b| {
        b.iter(|| Entry::from_bytes(black_box(&bytes)).unwrap())
    });
}

fn bench_verify_hash(c: &mut Criterion) {
    let op = GraphOp::AddNode {
        node_id: "server-1".into(),
        node_type: "entity".into(),
        subtype: None,
        label: "Production Server".into(),
        properties: BTreeMap::new(),
    };
    let clock = LamportClock { id: "inst-a".into(), time: 1 };
    let entry = Entry::new(op, vec![], vec![], clock, "inst-a");

    c.bench_function("entry_verify_hash", |b| {
        b.iter(|| black_box(entry.verify_hash()))
    });
}

criterion_group!(benches, bench_entry_creation, bench_entry_roundtrip, bench_verify_hash);
criterion_main!(benches);
