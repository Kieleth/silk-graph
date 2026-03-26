"""EXP-03: Comparative benchmarks — Silk vs Loro vs pycrdt (Yjs).

Measures shared CRDT operations (write, update, sync, merge) across
three systems. Each system uses its natural API. All measurements
are in-memory, single-threaded, on the same hardware.

Usage:
    python experiments/bench_comparative.py              # full run, text tables
    python experiments/bench_comparative.py --json       # JSON output
    python experiments/bench_comparative.py --only silk,loro
    pytest experiments/bench_comparative.py -v           # S5 correctness as test
"""

import platform
import statistics
import sys
import time

sys.path.insert(0, ".")
from experiments.adapters import CRDTAdapter, available_adapters
from experiments.harness import Metric, check_metrics, print_table, to_json


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def _timed(fn) -> float:
    """Run fn(), return elapsed milliseconds."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def _run_rounds(fn, rounds=5) -> dict:
    """Run fn() multiple rounds, return stats dict (ms)."""
    times = [_timed(fn) for _ in range(rounds)]
    return {
        "median_ms": round(statistics.median(times), 2),
        "mean_ms": round(statistics.mean(times), 2),
        "min_ms": round(min(times), 2),
        "max_ms": round(max(times), 2),
    }


# ---------------------------------------------------------------------------
# S1: Write throughput
# ---------------------------------------------------------------------------

def run_s1(adapter: CRDTAdapter, N: int, rounds: int = 5) -> dict:
    """Create N entities with 3 properties each."""
    def work():
        s = adapter.create_store(f"s1-{N}")
        for i in range(N):
            adapter.add_entity(s, f"e-{i}", {"name": f"node-{i}", "status": "active", "seq": i})

    stats = _run_rounds(work, rounds)
    ops_sec = int(N / (stats["median_ms"] / 1000)) if stats["median_ms"] > 0 else 0
    return {"system": adapter.name, "scenario": "S1_write", "N": N, "ops_sec": ops_sec, **stats}


# ---------------------------------------------------------------------------
# S2: Update throughput
# ---------------------------------------------------------------------------

def run_s2(adapter: CRDTAdapter, N: int, rounds: int = 5) -> dict:
    """Update one field N times on a single entity."""
    def work():
        s = adapter.create_store(f"s2-{N}")
        adapter.add_entity(s, "target", {"counter": 0})
        for i in range(N):
            adapter.update_field(s, "target", "counter", i)

    stats = _run_rounds(work, rounds)
    ops_sec = int(N / (stats["median_ms"] / 1000)) if stats["median_ms"] > 0 else 0
    return {"system": adapter.name, "scenario": "S2_update", "N": N, "ops_sec": ops_sec, **stats}


# ---------------------------------------------------------------------------
# S3: Sync latency
# ---------------------------------------------------------------------------

def run_s3(adapter: CRDTAdapter, M: int, rounds: int = 5) -> dict:
    """Two peers each write M entities, then bidirectional sync."""
    def work():
        a = adapter.create_store("s3-a")
        b = adapter.create_store("s3-b")
        for i in range(M):
            adapter.add_entity(a, f"a-{i}", {"name": f"a-{i}", "seq": i})
            adapter.add_entity(b, f"b-{i}", {"name": f"b-{i}", "seq": i})
        # Bidirectional sync
        adapter.sync_one_way(a, b)
        adapter.sync_one_way(b, a)

    stats = _run_rounds(work, rounds)
    return {"system": adapter.name, "scenario": "S3_sync", "M": M, **stats}


# ---------------------------------------------------------------------------
# S4: Sync bandwidth
# ---------------------------------------------------------------------------

def run_s4(adapter: CRDTAdapter, M: int) -> dict:
    """Measure bytes transferred for bidirectional sync of M entities."""
    a = adapter.create_store("s4-a")
    b = adapter.create_store("s4-b")
    for i in range(M):
        adapter.add_entity(a, f"a-{i}", {"name": f"a-{i}", "seq": i})
        adapter.add_entity(b, f"b-{i}", {"name": f"b-{i}", "seq": i})

    r_ab = adapter.sync_one_way(a, b)
    r_ba = adapter.sync_one_way(b, a)

    return {
        "system": adapter.name,
        "scenario": "S4_bandwidth",
        "M": M,
        "a_to_b_bytes": r_ab.bytes_sent,
        "b_to_a_bytes": r_ba.bytes_sent,
        "total_bytes": r_ab.bytes_sent + r_ba.bytes_sent,
    }


# ---------------------------------------------------------------------------
# S5: Merge correctness
# ---------------------------------------------------------------------------

def run_s5(adapter: CRDTAdapter, rounds: int = 10) -> dict:
    """Fork, concurrent update to same field, sync, verify convergence."""
    converged = 0
    for i in range(rounds):
        base = adapter.create_store(f"s5-base-{i}")
        adapter.add_entity(base, "shared", {"value": "original"})

        a = adapter.fork(base, f"s5-a-{i}")
        b = adapter.fork(base, f"s5-b-{i}")

        adapter.update_field(a, "shared", "value", f"from-a-{i}")
        adapter.update_field(b, "shared", "value", f"from-b-{i}")

        adapter.sync_one_way(a, b)
        adapter.sync_one_way(b, a)

        val_a = adapter.read_field(a, "shared", "value")
        val_b = adapter.read_field(b, "shared", "value")

        if val_a == val_b:
            converged += 1

    return {
        "system": adapter.name,
        "scenario": "S5_convergence",
        "rounds": rounds,
        "converged": converged,
        "rate": round(converged / rounds * 100, 1),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

WRITE_SCALES = [100, 1_000, 10_000]
SYNC_SCALES = [100, 500]


def run_all(adapters: list[CRDTAdapter], scenarios: list[str] | None = None):
    """Run all scenarios, return results by scenario."""
    results = {}

    if scenarios is None or "S1" in scenarios:
        print("\n--- S1: Write Throughput ---")
        rows = []
        for a in adapters:
            for n in WRITE_SCALES:
                rows.append(run_s1(a, n))
        results["S1"] = rows
        print_table(rows, ["system", "N", "median_ms", "ops_sec"])

    if scenarios is None or "S2" in scenarios:
        print("\n--- S2: Update Throughput ---")
        rows = []
        for a in adapters:
            for n in WRITE_SCALES:
                rows.append(run_s2(a, n))
        results["S2"] = rows
        print_table(rows, ["system", "N", "median_ms", "ops_sec"])

    if scenarios is None or "S3" in scenarios:
        print("\n--- S3: Sync Latency ---")
        rows = []
        for a in adapters:
            for m in SYNC_SCALES:
                rows.append(run_s3(a, m))
        results["S3"] = rows
        print_table(rows, ["system", "M", "median_ms"])

    if scenarios is None or "S4" in scenarios:
        print("\n--- S4: Sync Bandwidth ---")
        rows = []
        for a in adapters:
            for m in SYNC_SCALES:
                rows.append(run_s4(a, m))
        results["S4"] = rows
        print_table(rows, ["system", "M", "a_to_b_bytes", "b_to_a_bytes", "total_bytes"])

    if scenarios is None or "S5" in scenarios:
        print("\n--- S5: Merge Correctness ---")
        rows = []
        for a in adapters:
            rows.append(run_s5(a))
        results["S5"] = rows
        print_table(rows, ["system", "rounds", "converged", "rate"])

    return results


# ---------------------------------------------------------------------------
# Pytest: S5 correctness as regression test
# ---------------------------------------------------------------------------

def test_all_systems_converge():
    """All CRDT systems must achieve 100% convergence on concurrent updates."""
    adapters = available_adapters()
    metrics = []
    for a in adapters:
        result = run_s5(a)
        metrics.append(Metric(
            name=f"{a.name}_convergence_rate",
            measured=result["rate"],
            threshold=100.0,
            op="==",
            unit="%",
        ))
    check_metrics(metrics, label="EXP-03 merge correctness")


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    only = None
    scenarios = None
    for arg in sys.argv[1:]:
        if arg.startswith("--only="):
            only = arg.split("=")[1].split(",")
        elif arg.startswith("--scenario="):
            scenarios = [arg.split("=")[1]]

    adapters = available_adapters()
    if only:
        adapters = [a for a in adapters if a.name in only]

    print(f"EXP-03: Comparative CRDT Benchmarks")
    print(f"  systems: {', '.join(f'{a.name} v{a.version}' for a in adapters)}")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")

    results = run_all(adapters, scenarios)

    if "--json" in sys.argv:
        print("\n" + to_json({
            "experiment": "EXP-03_comparative",
            "platform": f"{platform.machine()} / {platform.system()}",
            "python": platform.python_version(),
            "systems": {a.name: a.version for a in adapters},
            "results": results,
        }))
