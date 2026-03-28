"""EXP-08: Persistence overhead — in-memory vs persistent store.

Measures the cost of redb persistence on write throughput, sync, and
startup (oplog reconstruction). Answers: how much does disk I/O cost?

Usage:
    python experiments/test_persistence_perf.py
    pytest experiments/test_persistence_perf.py -v
"""

import os
import statistics
import sys
import tempfile
import time

import pytest
from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import Metric, check_metrics, print_table


ONTOLOGY = {
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string"},
                "status": {"value_type": "string"},
                "seq": {"value_type": "int"},
            }
        }
    },
    "edge_types": {},
}


def _timed(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def measure_write(n, persistent, rounds=5):
    """Measure write throughput for in-memory or persistent store."""
    times = []
    for _ in range(rounds):
        if persistent:
            tmp = tempfile.NamedTemporaryFile(suffix=".redb", delete=False)
            tmp.close()
            path = tmp.name
        else:
            path = None

        def work():
            if path:
                s = GraphStore("bench", ONTOLOGY, path=path)
            else:
                s = GraphStore("bench", ONTOLOGY)
            for i in range(n):
                s.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "status": "active", "seq": i})

        times.append(_timed(work))

        if path:
            os.unlink(path)

    return round(statistics.median(times), 2)


def measure_startup(n, rounds=5):
    """Measure startup time: open persistent store, reconstruct oplog, rebuild graph."""
    # Create a store with n entities
    tmp = tempfile.NamedTemporaryFile(suffix=".redb", delete=False)
    tmp.close()
    path = tmp.name

    s = GraphStore("bench", ONTOLOGY, path=path)
    for i in range(n):
        s.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "status": "active", "seq": i})
    del s

    # Measure reopen time
    times = []
    for _ in range(rounds):
        times.append(_timed(lambda: GraphStore.open(path)))

    os.unlink(path)
    return round(statistics.median(times), 2)


def measure_sync_persistent(n, rounds=5):
    """Measure sync between two persistent stores."""
    times = []
    for _ in range(rounds):
        tmp_a = tempfile.NamedTemporaryFile(suffix=".redb", delete=False)
        tmp_b = tempfile.NamedTemporaryFile(suffix=".redb", delete=False)
        tmp_a.close()
        tmp_b.close()

        a = GraphStore("peer-a", ONTOLOGY, path=tmp_a.name)
        b = GraphStore("peer-b", ONTOLOGY, path=tmp_b.name)

        for i in range(n):
            a.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})

        t0 = time.perf_counter()
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)
        times.append((time.perf_counter() - t0) * 1000)

        del a, b
        os.unlink(tmp_a.name)
        os.unlink(tmp_b.name)

    return round(statistics.median(times), 2)


# ---------------------------------------------------------------------------
# Metric threshold
# ---------------------------------------------------------------------------
MAX_SYNC_PERSISTENCE_OVERHEAD = 10.0  # sync to persistent should be < 10x in-memory


def test_persistence_sync_overhead_bounded():
    """Sync to persistent store should be within 10x of in-memory."""
    # Sync batches entries → single transaction. Much less overhead than per-write.
    mem_times = []
    disk_times = []
    for _ in range(3):
        a = GraphStore("a", ONTOLOGY)
        b_mem = GraphStore("b", ONTOLOGY)
        for i in range(500):
            a.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})
        mem_times.append(_timed(lambda: b_mem.merge_sync_payload(a.receive_sync_offer(b_mem.generate_sync_offer()))))

    for _ in range(3):
        a = GraphStore("a", ONTOLOGY)
        for i in range(500):
            a.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})
        disk_times.append(measure_sync_persistent(500, rounds=1))

    mem = statistics.median(mem_times)
    disk = statistics.median(disk_times)
    ratio = disk / mem if mem > 0 else float("inf")

    check_metrics([
        Metric(
            name="persistence_sync_overhead",
            measured=round(ratio, 2),
            threshold=MAX_SYNC_PERSISTENCE_OVERHEAD,
            op="<",
            unit="x",
        ),
    ], label="EXP-08 persistence sync overhead")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import platform
    print(f"EXP-08: Persistence Overhead")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")
    print()

    scales = [100, 500, 1000]
    results = []

    for n in scales:
        mem = measure_write(n, persistent=False)
        disk = measure_write(n, persistent=True)
        sync_mem = None
        sync_disk = measure_sync_persistent(n)
        startup = measure_startup(n)

        # In-memory sync for comparison
        def _sync_mem():
            a = GraphStore("a", ONTOLOGY)
            b = GraphStore("b", ONTOLOGY)
            for i in range(n):
                a.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})
            offer = b.generate_sync_offer()
            payload = a.receive_sync_offer(offer)
            b.merge_sync_payload(payload)

        sync_mem_times = [_timed(_sync_mem) for _ in range(5)]
        sync_mem = round(statistics.median(sync_mem_times), 2)

        results.append({
            "N": n,
            "write_mem_ms": mem,
            "write_disk_ms": disk,
            "write_ratio": round(disk / mem, 2) if mem > 0 else 0,
            "sync_mem_ms": sync_mem,
            "sync_disk_ms": sync_disk,
            "sync_ratio": round(sync_disk / sync_mem, 2) if sync_mem > 0 else 0,
            "startup_ms": startup,
        })

    print_table(results, [
        "N", "write_mem_ms", "write_disk_ms", "write_ratio",
        "sync_mem_ms", "sync_disk_ms", "sync_ratio", "startup_ms",
    ])
