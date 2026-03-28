"""EXP-07: Graph system comparison — Silk vs NetworkX vs TerminusDB.

Compares Silk against a plain graph library (NetworkX, baseline) and a
server-based versioned graph database (TerminusDB) on shared operations.

NetworkX shows the floor cost — no CRDT, no sync, no schema, no persistence.
TerminusDB shows the server-based alternative — schema + versioning + sync
but requires Docker and communicates over HTTP.

Usage:
    python experiments/test_graph_comparison.py
    pytest experiments/test_graph_comparison.py -v
"""

import copy
import os
import pickle
import statistics
import sys
import time

import pytest

sys.path.insert(0, ".")
from experiments.harness import Metric, check_metrics, print_table

# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

TERMINUSDB_URL = os.environ.get("TERMINUSDB_URL", "http://127.0.0.1:6363/")
TERMINUSDB_PASS = os.environ.get("TERMINUSDB_PASS", "bench123")


def _silk_available():
    try:
        from silk import GraphStore
        return True
    except ImportError:
        return False


def _terminusdb_available():
    try:
        from terminusdb import Client
        c = Client(TERMINUSDB_URL)
        c.connect(user="admin", key=TERMINUSDB_PASS)
        c.info()
        return True
    except Exception:
        return False


def _timed(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Silk operations
# ---------------------------------------------------------------------------

def silk_write(n):
    from silk import GraphStore
    ont = {"node_types": {"entity": {"properties": {"name": {"value_type": "string"}, "status": {"value_type": "string"}, "seq": {"value_type": "int"}}}}, "edge_types": {}}
    s = GraphStore("bench", ont)
    for i in range(n):
        s.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "status": "active", "seq": i})
    return s


def silk_update(s, n):
    for i in range(n):
        s.update_property(f"n-{i % 100}", "status", f"updated-{i}")


def silk_query_all(s):
    return s.all_nodes()


def silk_snapshot_size(s):
    return len(s.snapshot())


def silk_bfs(s, start):
    return s.bfs(start)


def silk_dfs(s, start):
    return s.dfs(start)


# ---------------------------------------------------------------------------
# NetworkX operations
# ---------------------------------------------------------------------------

def nx_write(n):
    import networkx as nx
    g = nx.DiGraph()
    for i in range(n):
        g.add_node(f"n-{i}", name=f"node-{i}", status="active", seq=i, _type="entity")
    return g


def nx_update(g, n):
    for i in range(n):
        g.nodes[f"n-{i % 100}"]["status"] = f"updated-{i}"


def nx_query_all(g):
    return list(g.nodes(data=True))


def nx_snapshot_size(g):
    return len(pickle.dumps(g))


def nx_bfs(g, start):
    import networkx as nx
    return list(nx.bfs_tree(g, start))


def nx_dfs(g, start):
    import networkx as nx
    return list(nx.dfs_tree(g, start))


# ---------------------------------------------------------------------------
# TerminusDB operations
# ---------------------------------------------------------------------------

def tdb_setup(db_name):
    from terminusdb import Client
    c = Client(TERMINUSDB_URL)
    c.connect(user="admin", key=TERMINUSDB_PASS)
    try:
        c.delete_database(db_name)
    except Exception:
        pass
    c.create_database(db_name)
    c.insert_document({
        "@type": "Class", "@id": "Entity",
        "name": "xsd:string", "status": "xsd:string", "seq": "xsd:integer",
    }, graph_type="schema")
    return c


def tdb_write(c, n):
    docs = [{"@type": "Entity", "name": f"node-{i}", "status": "active", "seq": i} for i in range(n)]
    c.insert_document(docs)


def tdb_update(c, doc_id, key, value):
    doc = c.get_document(doc_id)
    doc[key] = value
    c.replace_document(doc)


def tdb_query_all(c):
    return list(c.query_document({"@type": "Entity"}))


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_comparison(n=1000, rounds=5):
    """Run write/query/snapshot comparison across all available systems."""
    results = []

    # --- Silk ---
    if _silk_available():
        write_times = []
        query_times = []
        update_times = []
        for _ in range(rounds):
            t = _timed(lambda: silk_write(n))
            write_times.append(t)
        s = silk_write(n)
        for _ in range(rounds):
            query_times.append(_timed(lambda: silk_query_all(s)))
        for _ in range(rounds):
            update_times.append(_timed(lambda: silk_update(s, 100)))
        snap = silk_snapshot_size(s)
        mem = s.memory_usage()["total_bytes"]

        results.append({
            "system": "silk",
            "write_ms": round(statistics.median(write_times), 2),
            "write_ops_sec": int(n / (statistics.median(write_times) / 1000)),
            "query_ms": round(statistics.median(query_times), 2),
            "update_100_ms": round(statistics.median(update_times), 2),
            "snapshot_kb": round(snap / 1024, 1),
            "memory_kb": round(mem / 1024, 1),
        })

    # --- NetworkX ---
    write_times = []
    query_times = []
    update_times = []
    for _ in range(rounds):
        write_times.append(_timed(lambda: nx_write(n)))
    g = nx_write(n)
    for _ in range(rounds):
        query_times.append(_timed(lambda: nx_query_all(g)))
    for _ in range(rounds):
        update_times.append(_timed(lambda: nx_update(g, 100)))
    snap = nx_snapshot_size(g)

    results.append({
        "system": "networkx",
        "write_ms": round(statistics.median(write_times), 2),
        "write_ops_sec": int(n / (statistics.median(write_times) / 1000)),
        "query_ms": round(statistics.median(query_times), 2),
        "update_100_ms": round(statistics.median(update_times), 2),
        "snapshot_kb": round(snap / 1024, 1),
        "memory_kb": round(snap / 1024, 1),  # pickle ≈ in-memory for NetworkX
    })

    # --- TerminusDB ---
    if _terminusdb_available():
        write_times = []
        query_times = []
        for _ in range(rounds):
            c = tdb_setup(f"bench_comp_{_}")
            write_times.append(_timed(lambda: tdb_write(c, n)))
        c = tdb_setup("bench_comp_q")
        tdb_write(c, n)
        for _ in range(rounds):
            query_times.append(_timed(lambda: tdb_query_all(c)))

        results.append({
            "system": "terminusdb",
            "write_ms": round(statistics.median(write_times), 2),
            "write_ops_sec": int(n / (statistics.median(write_times) / 1000)),
            "query_ms": round(statistics.median(query_times), 2),
            "update_100_ms": "N/A",
            "snapshot_kb": "N/A",
            "memory_kb": "N/A (server)",
        })

    return results


# ---------------------------------------------------------------------------
# Traversal comparison (Silk vs NetworkX only — TerminusDB has no client-side traversal)
# ---------------------------------------------------------------------------

def run_traversal_comparison(n=1000, rounds=10):
    """Compare BFS/DFS between Silk and NetworkX on a chain graph."""
    from silk import GraphStore
    import networkx as nx

    # Build chain: n0 -> n1 -> n2 -> ... -> n999
    ont = {"node_types": {"entity": {"properties": {}}}, "edge_types": {"NEXT": {"source_types": ["entity"], "target_types": ["entity"]}}}
    s = GraphStore("trav", ont)
    g = nx.DiGraph()
    for i in range(n):
        s.add_node(f"n-{i}", "entity", f"N{i}")
        g.add_node(f"n-{i}")
    for i in range(n - 1):
        s.add_edge(f"e-{i}", "NEXT", f"n-{i}", f"n-{i+1}")
        g.add_edge(f"n-{i}", f"n-{i+1}")

    results = []
    for name, silk_fn, nx_fn in [
        ("BFS", lambda: s.bfs("n-0"), lambda: list(nx.bfs_tree(g, "n-0"))),
        ("DFS", lambda: s.dfs("n-0"), lambda: list(nx.dfs_tree(g, "n-0"))),
    ]:
        silk_times = [_timed(silk_fn) for _ in range(rounds)]
        nx_times = [_timed(nx_fn) for _ in range(rounds)]
        results.append({
            "algorithm": name,
            "silk_ms": round(statistics.median(silk_times), 3),
            "networkx_ms": round(statistics.median(nx_times), 3),
            "ratio": round(statistics.median(silk_times) / statistics.median(nx_times), 2) if statistics.median(nx_times) > 0 else 0,
        })
    return results


# ---------------------------------------------------------------------------
# Pytest: basic regression
# ---------------------------------------------------------------------------

def test_silk_faster_than_terminusdb_on_write():
    """Silk (embedded) should be faster than TerminusDB (server) on writes."""
    if not _terminusdb_available():
        pytest.skip("TerminusDB not running")
    results = run_comparison(n=500, rounds=3)
    silk = next((r for r in results if r["system"] == "silk"), None)
    tdb = next((r for r in results if r["system"] == "terminusdb"), None)
    if silk is None or tdb is None:
        pytest.skip("silk or terminusdb not available")
    check_metrics([
        Metric(
            name="silk_vs_terminusdb_write",
            measured=round(silk["write_ms"] / tdb["write_ms"], 2),
            threshold=1.0,
            op="<",
            unit="x (lower = silk faster)",
        ),
    ], label="EXP-07 silk vs terminusdb write")


def test_networkx_baseline_overhead():
    """Silk should be within 12x of NetworkX on writes (CRDT overhead)."""
    results = run_comparison(n=1000, rounds=3)
    silk = next((r for r in results if r["system"] == "silk"), None)
    nxr = next((r for r in results if r["system"] == "networkx"), None)
    if silk is None or nxr is None:
        pytest.skip("silk or networkx not available")
    ratio = silk["write_ms"] / nxr["write_ms"] if nxr["write_ms"] > 0 else float("inf")
    check_metrics([
        Metric(
            name="silk_vs_networkx_write_overhead",
            measured=round(ratio, 1),
            threshold=12.0,
            op="<",
            unit="x",
        ),
    ], label="EXP-07 CRDT overhead vs plain graph")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import platform
    print(f"EXP-07: Graph System Comparison")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")
    print(f"  terminusdb: {'available' if _terminusdb_available() else 'not running'}")
    print()

    print("--- Write / Query / Snapshot (1000 entities) ---")
    results = run_comparison(n=1000, rounds=5)
    print_table(results, ["system", "write_ms", "write_ops_sec", "query_ms", "update_100_ms", "snapshot_kb", "memory_kb"])

    print()
    print("--- Traversal: BFS/DFS on 1000-node chain ---")
    trav = run_traversal_comparison(n=1000, rounds=10)
    print_table(trav, ["algorithm", "silk_ms", "networkx_ms", "ratio"])
