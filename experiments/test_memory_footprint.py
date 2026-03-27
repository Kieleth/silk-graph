"""EXP-04: Memory footprint at scale.

Measures Silk's memory usage as graph size grows. The reviewer flagged
that OpLog + MaterializedGraph are fully in-memory with no lazy loading.
This experiment quantifies the actual cost.

Usage:
    python experiments/test_memory_footprint.py
    pytest experiments/test_memory_footprint.py -v
"""

import gc
import sys
import tracemalloc

import pytest
from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import Metric, check_metrics, print_table


ONTOLOGY = {
    "node_types": {
        "server": {
            "properties": {
                "hostname": {"value_type": "string"},
                "ip": {"value_type": "string"},
                "status": {"value_type": "string"},
                "cpu_cores": {"value_type": "int"},
                "ram_gb": {"value_type": "int"},
            }
        },
        "service": {
            "properties": {
                "name": {"value_type": "string"},
                "version": {"value_type": "string"},
                "port": {"value_type": "int"},
            }
        },
    },
    "edge_types": {
        "RUNS_ON": {
            "source_types": ["service"],
            "target_types": ["server"],
        },
        "DEPENDS_ON": {
            "source_types": ["service"],
            "target_types": ["service"],
        },
    },
}


def measure_memory(num_servers: int, services_per_server: int) -> dict:
    """Create a realistic infrastructure graph and measure memory.

    Each server has `services_per_server` services, each service has a
    RUNS_ON edge to its server. Services also have 1-2 DEPENDS_ON edges
    to other services (creating a dependency mesh).
    """
    import random
    rng = random.Random(42)

    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]

    store = GraphStore("mem-test", ONTOLOGY)

    # Create servers
    for i in range(num_servers):
        store.add_node(f"srv-{i}", "server", f"Server {i}", {
            "hostname": f"srv-{i}.internal",
            "ip": f"10.0.{i // 256}.{i % 256}",
            "status": rng.choice(["active", "standby", "maintenance"]),
            "cpu_cores": rng.choice([4, 8, 16, 32]),
            "ram_gb": rng.choice([16, 32, 64, 128]),
        })

    # Create services on each server
    service_ids = []
    for i in range(num_servers):
        for j in range(services_per_server):
            sid = f"svc-{i}-{j}"
            store.add_node(sid, "service", f"Service {i}-{j}", {
                "name": f"app-{j}",
                "version": f"{rng.randint(1,5)}.{rng.randint(0,20)}.{rng.randint(0,99)}",
                "port": 3000 + j,
            })
            store.add_edge(f"runs-{i}-{j}", "RUNS_ON", sid, f"srv-{i}")
            service_ids.append(sid)

    # Create dependency edges (1-2 per service)
    edge_count = 0
    for sid in service_ids:
        num_deps = rng.randint(1, 2)
        targets = rng.sample(service_ids, min(num_deps, len(service_ids)))
        for t in targets:
            if t != sid:
                store.add_edge(f"dep-{edge_count}", "DEPENDS_ON", sid, t)
                edge_count += 1

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    py_used = current - baseline
    total_nodes = num_servers + num_servers * services_per_server
    total_edges = num_servers * services_per_server + edge_count

    # Rust-side memory (oplog + materialized graph)
    rust_mem = store.memory_usage()

    return {
        "servers": num_servers,
        "services_per_server": services_per_server,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "oplog_entries": store.len(),
        "rust_total_mb": round(rust_mem["total_bytes"] / (1024 * 1024), 2),
        "rust_oplog_mb": round(rust_mem["oplog_bytes"] / (1024 * 1024), 2),
        "rust_graph_mb": round(rust_mem["graph_bytes"] / (1024 * 1024), 2),
        "py_overhead_mb": round(py_used / (1024 * 1024), 2),
        "bytes_per_node": round(rust_mem["total_bytes"] / total_nodes) if total_nodes > 0 else 0,
        "snapshot_mb": round(len(store.snapshot()) / (1024 * 1024), 2),
    }


SCALES = [
    (10, 3),       # 40 nodes — tiny
    (100, 3),      # 400 nodes — small
    (500, 3),      # 2000 nodes — medium
    (1000, 3),     # 4000 nodes — large
    (5000, 3),     # 20000 nodes — stress
    (10000, 2),    # 30000 nodes — upper bound
]


# ---------------------------------------------------------------------------
# Metric thresholds
# ---------------------------------------------------------------------------
MAX_BYTES_PER_NODE = 5000       # 5 KB per node (with properties + edges)
MAX_MB_AT_10K_SERVERS = 500     # 500 MB ceiling at 10K servers


def test_memory_scales_linearly():
    """Memory usage should scale roughly linearly with graph size."""
    small = measure_memory(100, 3)
    large = measure_memory(1000, 3)

    # 10x more nodes should use roughly 10x more memory (allow 15x for overhead)
    ratio = large["rust_total_mb"] / small["rust_total_mb"] if small["rust_total_mb"] > 0 else float("inf")

    check_metrics([
        Metric(
            name="memory_scaling_ratio",
            measured=round(ratio, 1),
            threshold=15.0,
            op="<",
            unit="x (10x nodes)",
        ),
        Metric(
            name="bytes_per_node_small",
            measured=small["bytes_per_node"],
            threshold=MAX_BYTES_PER_NODE,
            op="<",
            unit="bytes",
        ),
        Metric(
            name="bytes_per_node_large",
            measured=large["bytes_per_node"],
            threshold=MAX_BYTES_PER_NODE,
            op="<",
            unit="bytes",
        ),
    ], label="EXP-04 memory scaling")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import platform
    print(f"EXP-04: Memory Footprint at Scale")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")
    print()

    results = []
    for servers, sps in SCALES:
        print(f"  Measuring {servers} servers × {sps} services...", end=" ", flush=True)
        r = measure_memory(servers, sps)
        print(f"{r['rust_total_mb']} MB ({r['total_nodes']} nodes, {r['total_edges']} edges)")
        results.append(r)

    print()
    print_table(results, [
        "servers", "total_nodes", "total_edges", "oplog_entries",
        "rust_total_mb", "rust_oplog_mb", "rust_graph_mb",
        "py_overhead_mb", "bytes_per_node", "snapshot_mb",
    ])

    if len(results) >= 2:
        large = results[-1]
        print(f"\nAt {large['total_nodes']} nodes / {large['total_edges']} edges:")
        print(f"  Rust total: {large['rust_total_mb']} MB (oplog: {large['rust_oplog_mb']}, graph: {large['rust_graph_mb']})")
        print(f"  Python overhead: {large['py_overhead_mb']} MB")
        print(f"  Bytes per node: {large['bytes_per_node']}")
        print(f"  Snapshot: {large['snapshot_mb']} MB")
        if large['total_nodes'] > 0:
            projected_100k = round(large['bytes_per_node'] * 100_000 / (1024 * 1024), 1)
            print(f"  Projected at 100K nodes: ~{projected_100k} MB")
