"""F-10: Sync overlap cost experiment.

Demonstrates that sync time scales with OVERLAP (shared entries),
not with DELTA (entries to send). This is inverted from expected behavior.

Root cause: Phase 2 ancestor closure in entries_missing() (src/sync.rs:222-247)
walks all parents unconditionally, ignoring the bloom filter. At high overlap,
the closure re-walks the entire shared DAG.

Usage:
    # As pytest (xfail until fix lands):
    pytest experiments/test_sync_overlap.py -v

    # As standalone script (prints table):
    python experiments/test_sync_overlap.py

    # With JSON output:
    python experiments/test_sync_overlap.py --json
"""

import platform
import sys
import time

import pytest

from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import (
    Metric,
    SyncMeasurement,
    check_metrics,
    measure,
    measure_sync_phase,
    print_table,
    to_json,
)

ONTOLOGY = {
    "node_types": {
        "entity": {
            "properties": {
                "seq": {"value_type": "int"},
            }
        }
    },
    "edge_types": {},
}

OVERLAP_LEVELS = [0, 10, 25, 50, 75, 90, 95, 99]
DEFAULT_TOTAL_NODES = 1000
DEFAULT_ROUNDS = 5


def make_overlap_scenario(total_nodes: int, overlap_pct: int):
    """Create two stores with controlled overlap.

    Returns (store_a, store_b) where:
    - store_a has total_nodes entries (all unique to A after the shared prefix)
    - store_b has shared + unique_b entries
    - shared = total_nodes * overlap_pct / 100

    Both stores have the same genesis. The shared entries are the first
    `shared` nodes written by A, snapshotted into B.
    """
    shared = int(total_nodes * overlap_pct / 100)
    unique_a = total_nodes - shared
    unique_b = total_nodes - shared

    store_a = GraphStore("peer-a", ONTOLOGY)

    # A writes shared nodes first
    for i in range(shared):
        store_a.add_node(f"shared-{i}", "entity", f"S-{i}", {"seq": i})

    # Snapshot A at this point -> B starts with shared entries
    store_b = GraphStore.from_snapshot("peer-b", store_a.snapshot())

    # A continues writing its unique nodes
    for i in range(unique_a):
        store_a.add_node(f"a-{i}", "entity", f"A-{i}", {"seq": shared + i})

    # B writes its own unique nodes
    for i in range(unique_b):
        store_b.add_node(f"b-{i}", "entity", f"B-{i}", {"seq": shared + i})

    return store_a, store_b


def run_scenario(total_nodes: int, overlap_pct: int, rounds: int = 5) -> dict:
    """Run one overlap scenario, return measurement dict.

    Creates fresh stores for each round to avoid measuring already-synced state.
    Measures A→B direction (A sends to B what B is missing).
    """
    shared = int(total_nodes * overlap_pct / 100)
    unique_a = total_nodes - shared

    receive_times = []
    total_times = []
    payload_sizes = []
    entries_counts = []

    for _ in range(rounds):
        store_a, store_b = make_overlap_scenario(total_nodes, overlap_pct)
        m = measure_sync_phase(store_a, store_b)
        receive_times.append(m.receive_ms)
        total_times.append(m.total_ms)
        payload_sizes.append(m.payload_bytes)
        entries_counts.append(m.entries_sent)

    import statistics

    return {
        "overlap_pct": overlap_pct,
        "shared": shared,
        "unique_a": unique_a,
        "entries_sent": entries_counts[0] if entries_counts else 0,
        "receive_ms": round(statistics.median(receive_times), 2),
        "total_ms": round(statistics.median(total_times), 2),
        "payload_bytes": payload_sizes[0] if payload_sizes else 0,
        "receive_times": receive_times,
    }


def run_all_scenarios(
    total_nodes: int = DEFAULT_TOTAL_NODES,
    rounds: int = DEFAULT_ROUNDS,
    overlap_levels: list[int] = None,
) -> list[dict]:
    """Run all overlap scenarios, return list of result dicts."""
    if overlap_levels is None:
        overlap_levels = OVERLAP_LEVELS

    results = []
    for pct in overlap_levels:
        result = run_scenario(total_nodes, pct, rounds)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# TDD Test — expected to FAIL before the fix
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metric thresholds — adjust these if hardware or protocol changes
# ---------------------------------------------------------------------------
OVERLAP_RATIO_THRESHOLD = 2.0   # max acceptable ratio: time_90% / time_10%
MAX_RECEIVE_MS_1K_NODES = 10.0  # max receive_ms for 1000 nodes at any overlap


def test_sync_overlap_cost_sublinear():
    """Sync receive time should scale with delta (entries to send),
    not with overlap (shared entries).

    At 10% overlap: 900 entries to send, 100 shared
    At 90% overlap: 100 entries to send, 900 shared

    If scaling is proportional to delta (correct):
        90% overlap should be FASTER than 10% overlap
    """
    total = 1000
    rounds = 5

    low = run_scenario(total, overlap_pct=10, rounds=rounds)
    high = run_scenario(total, overlap_pct=90, rounds=rounds)

    ratio = high["receive_ms"] / low["receive_ms"] if low["receive_ms"] > 0 else float("inf")

    check_metrics([
        Metric(
            name="overlap_scaling_ratio",
            measured=round(ratio, 2),
            threshold=OVERLAP_RATIO_THRESHOLD,
            op="<",
            unit="x",
        ),
        Metric(
            name="receive_ms_10pct_overlap",
            measured=low["receive_ms"],
            threshold=MAX_RECEIVE_MS_1K_NODES,
            op="<",
            unit="ms",
        ),
        Metric(
            name="receive_ms_90pct_overlap",
            measured=high["receive_ms"],
            threshold=MAX_RECEIVE_MS_1K_NODES,
            op="<",
            unit="ms",
        ),
    ], label="EXP-01 sync overlap cost")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    total = DEFAULT_TOTAL_NODES
    rounds = DEFAULT_ROUNDS

    print(f"Silk Sync Overlap Experiment")
    print(f"  total_nodes: {total}")
    print(f"  rounds: {rounds}")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")
    print()

    results = run_all_scenarios(total, rounds)

    headers = ["overlap_pct", "shared", "unique_a", "entries_sent", "receive_ms", "total_ms", "payload_bytes"]
    print_table(results, headers)

    # Highlight the scaling problem
    print()
    if len(results) >= 2:
        low = next((r for r in results if r["overlap_pct"] == 10), results[0])
        high = next((r for r in results if r["overlap_pct"] == 90), results[-1])
        ratio = high["receive_ms"] / low["receive_ms"] if low["receive_ms"] > 0 else float("inf")
        print(f"Scaling ratio (90% / 10% overlap): {ratio:.1f}x")
        if ratio > 2.0:
            print(f"  -> INVERTED: high overlap is {ratio:.1f}x slower despite sending fewer entries")
        else:
            print(f"  -> CORRECT: high overlap is faster (less to send)")

    if "--json" in sys.argv:
        print()
        print(to_json({
            "experiment": "sync_overlap_cost",
            "total_nodes": total,
            "rounds": rounds,
            "platform": f"{platform.machine()} / {platform.system()}",
            "scenarios": results,
        }))
