"""C-1.0: Baseline append throughput BEFORE tail subscription machinery.

Captures the append throughput with no subscribers, to compare against the
same measurement after C-1.2 adds notify_waiters() to the append path.

If C-1.2 shows <1% overhead, the tail subscription API is always-on with
no config flag. If >1%, add an enable/disable toggle.

Baseline (2026-04-12, Apple Silicon, silk-graph 0.1.7):
  100 appends:     0.31 ms  (  321,586 ops/sec)
  1000 appends:    3.37 ms  (  297,046 ops/sec)
  10000 appends:  41.80 ms  (  239,251 ops/sec)

Post-C-1.2 measurement goes here.

Usage:
    python experiments/test_tail_overhead.py
    pytest experiments/test_tail_overhead.py -v
"""

import statistics
import sys
import time

import pytest
from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import measure


ONTOLOGY = {
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string"},
                "seq": {"value_type": "int"},
            }
        }
    },
    "edge_types": {},
}


def _bench_append(n: int, rounds: int = 10) -> float:
    """Return median ms to append n nodes to an in-memory store."""
    def work():
        store = GraphStore("bench", ONTOLOGY)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})

    stats = measure(work, rounds=rounds, warmup=2)
    return stats.median_ms()


def test_baseline_append_1k():
    """Baseline: 1000 appends to in-memory store."""
    median_ms = _bench_append(1000)
    # No assertion — this is a measurement baseline captured in CI logs.
    # Save to a file so the C-1.2 comparison test can diff against it.
    print(f"\nBASELINE 1000 appends: {median_ms:.2f} ms median")
    assert median_ms > 0


def test_baseline_append_10k():
    """Baseline: 10,000 appends to in-memory store."""
    median_ms = _bench_append(10_000, rounds=5)
    print(f"\nBASELINE 10,000 appends: {median_ms:.2f} ms median")
    print(f"BASELINE throughput: {10_000 / (median_ms / 1000):,.0f} appends/sec")
    assert median_ms > 0


if __name__ == "__main__":
    print("=" * 60)
    print("C-1.0: Baseline append throughput (no tail subscription)")
    print("=" * 60)

    for n in [100, 1_000, 10_000]:
        rounds = 20 if n <= 1000 else 5
        median_ms = _bench_append(n, rounds=rounds)
        throughput = n / (median_ms / 1000)
        print(f"  {n:>6} appends: {median_ms:>8.2f} ms  ({throughput:>10,.0f} ops/sec)")

    print()
    print("Save these numbers. After C-1.2 adds notify_waiters() hooks,")
    print("rerun and compare. If overhead >1%, add a config flag.")
