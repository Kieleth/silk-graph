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
# S6: Structured workload — entities + relationships + updates
# ---------------------------------------------------------------------------

def run_s6(adapter: CRDTAdapter, num_users: int, num_projects: int, rounds: int = 5) -> dict:
    """Simulate a project tracker: users, projects, assignments, status updates.

    Creates num_users users and num_projects projects, assigns each user
    to 1-3 projects, then updates project statuses. Measures total time.
    """
    import random

    def work():
        rng = random.Random(42)  # deterministic
        s = adapter.create_store(f"s6-{num_users}-{num_projects}")

        # Create users
        for i in range(num_users):
            adapter.add_entity(s, f"u-{i}", {"name": f"user-{i}", "role": rng.choice(["eng", "pm", "design"])})

        # Create projects
        for i in range(num_projects):
            adapter.add_entity(s, f"p-{i}", {"name": f"project-{i}", "status": "planning"})

        # Assign users to 1-3 projects each
        rel_count = 0
        for i in range(num_users):
            assigned = rng.sample(range(num_projects), min(rng.randint(1, 3), num_projects))
            for p in assigned:
                adapter.add_relationship(s, f"r-{rel_count}", "ASSIGNED_TO", f"u-{i}", f"p-{p}")
                rel_count += 1

        # Update project statuses
        for i in range(num_projects):
            adapter.update_field(s, f"p-{i}", "status", rng.choice(["active", "blocked", "done"]))

        return s, rel_count

    # Measure
    times = []
    last_rel_count = 0
    for _ in range(rounds):
        t0 = time.perf_counter()
        s, last_rel_count = work()
        times.append((time.perf_counter() - t0) * 1000)

    snap_size = adapter.snapshot_size(s)
    total_ops = num_users + num_projects + last_rel_count + num_projects  # entities + rels + updates

    return {
        "system": adapter.name,
        "scenario": "S6_structured",
        "users": num_users,
        "projects": num_projects,
        "relationships": last_rel_count,
        "total_ops": total_ops,
        "median_ms": round(statistics.median(times), 2),
        "ops_sec": int(total_ops / (statistics.median(times) / 1000)) if statistics.median(times) > 0 else 0,
        "snapshot_bytes": snap_size,
    }


# ---------------------------------------------------------------------------
# S7: Multi-peer convergence — N peers, ring sync
# ---------------------------------------------------------------------------

def run_s7(adapter: CRDTAdapter, num_peers: int, entities_per_peer: int, rounds: int = 3) -> dict:
    """N peers each write unique entities, then ring-sync until converged.

    Each peer writes entities_per_peer entities. Then peers sync in a ring
    (0→1→2→...→N-1→0) repeatedly until all peers have the same snapshot size.
    Measures: total sync time, sync rounds to converge, final snapshot size.
    """
    def work():
        peers = []
        for i in range(num_peers):
            s = adapter.create_store(f"s7-peer-{i}")
            for j in range(entities_per_peer):
                adapter.add_entity(s, f"peer{i}-e{j}", {"origin": f"peer-{i}", "seq": j})
            peers.append(s)

        # Ring sync until converged
        sync_rounds = 0
        total_sync_ms = 0
        total_bytes = 0
        for _ in range(num_peers * 2):  # upper bound
            sync_rounds += 1
            round_bytes = 0
            for i in range(num_peers):
                next_i = (i + 1) % num_peers
                t0 = time.perf_counter()
                r = adapter.sync_one_way(peers[i], peers[next_i])
                total_sync_ms += (time.perf_counter() - t0) * 1000
                round_bytes += r.bytes_sent

            total_bytes += round_bytes
            if round_bytes == 0:
                break  # converged — no more data to send

        snap_size = adapter.snapshot_size(peers[0])
        return sync_rounds, total_sync_ms, total_bytes, snap_size

    results = [work() for _ in range(rounds)]
    med_rounds = statistics.median([r[0] for r in results])
    med_sync_ms = statistics.median([r[1] for r in results])
    med_bytes = statistics.median([r[2] for r in results])
    snap = results[0][3]

    return {
        "system": adapter.name,
        "scenario": "S7_multi_peer",
        "peers": num_peers,
        "entities_per_peer": entities_per_peer,
        "total_entities": num_peers * entities_per_peer,
        "sync_rounds": int(med_rounds),
        "sync_ms": round(med_sync_ms, 2),
        "total_bytes": int(med_bytes),
        "snapshot_bytes": snap,
    }


# ---------------------------------------------------------------------------
# S8: Diverge-then-heal — two peers accumulate divergence, then sync
# ---------------------------------------------------------------------------

def run_s8(adapter: CRDTAdapter, shared: int, divergent_per_peer: int, rounds: int = 5) -> dict:
    """Two peers start from shared state, each writes independently, then heal.

    Measures sync cost as a function of divergence depth.
    """
    def work():
        # Build shared base
        base = adapter.create_store("s8-base")
        for i in range(shared):
            adapter.add_entity(base, f"shared-{i}", {"name": f"s-{i}", "seq": i})

        # Fork into two peers
        a = adapter.fork(base, "s8-a")
        b = adapter.fork(base, "s8-b")

        # Each diverges
        for i in range(divergent_per_peer):
            adapter.add_entity(a, f"a-{i}", {"name": f"a-{i}", "seq": i})
            adapter.add_entity(b, f"b-{i}", {"name": f"b-{i}", "seq": i})

        # Heal: bidirectional sync
        t0 = time.perf_counter()
        r_ab = adapter.sync_one_way(a, b)
        r_ba = adapter.sync_one_way(b, a)
        heal_ms = (time.perf_counter() - t0) * 1000

        return heal_ms, r_ab.bytes_sent + r_ba.bytes_sent

    results = [work() for _ in range(rounds)]
    med_ms = statistics.median([r[0] for r in results])
    med_bytes = statistics.median([r[1] for r in results])

    return {
        "system": adapter.name,
        "scenario": "S8_diverge_heal",
        "shared": shared,
        "divergent": divergent_per_peer,
        "heal_ms": round(med_ms, 2),
        "heal_bytes": int(med_bytes),
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

    if scenarios is None or "S6" in scenarios:
        print("\n--- S6: Structured Workload (users + projects + assignments + updates) ---")
        rows = []
        s6_configs = [(50, 10), (200, 40), (1000, 200)]
        for a in adapters:
            for users, projects in s6_configs:
                rows.append(run_s6(a, users, projects))
        results["S6"] = rows
        print_table(rows, ["system", "users", "projects", "relationships", "total_ops", "median_ms", "ops_sec", "snapshot_bytes"])

    if scenarios is None or "S7" in scenarios:
        print("\n--- S7: Multi-Peer Ring Convergence ---")
        rows = []
        s7_configs = [(3, 100), (5, 100), (10, 50)]
        for a in adapters:
            for peers, per_peer in s7_configs:
                rows.append(run_s7(a, peers, per_peer))
        results["S7"] = rows
        print_table(rows, ["system", "peers", "total_entities", "sync_rounds", "sync_ms", "total_bytes"])

    if scenarios is None or "S8" in scenarios:
        print("\n--- S8: Diverge-Then-Heal ---")
        rows = []
        s8_configs = [(100, 50), (500, 200), (1000, 500)]
        for a in adapters:
            for shared, div in s8_configs:
                rows.append(run_s8(a, shared, div))
        results["S8"] = rows
        print_table(rows, ["system", "shared", "divergent", "heal_ms", "heal_bytes"])

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
