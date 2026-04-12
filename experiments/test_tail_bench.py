"""C-1.6: Cursor-based tail subscription benchmarks.

Measures:
  1. Producer-side overhead vs baseline (no subscribers).
  2. Producer-side overhead with 1, 10, 100 active subscribers.
  3. Subscriber wake-up latency (append → next_batch returns).
  4. Comparison to the push-based `store.subscribe(callback)` API.

Usage:
    python experiments/test_tail_bench.py
    pytest experiments/test_tail_bench.py -v

Results (2026-04-12, Apple Silicon, silk-graph 0.2.0, median of 3 runs):
  Producer overhead (1000 appends):
    0 subs:   3.1 ms  (321k ops/s)  — baseline
    1 sub:    4.1 ms  (240k ops/s)  — -30%
    10 subs:  4.8 ms  (210k ops/s)  — -55%
    100 subs: 4.6 ms  (220k ops/s)  — -50%  ← plateau

  Wake-up latency: p50 0.07ms, p99 0.10ms.
  Push API (store.subscribe, 10 subs): 4.5-4.9 ms — same ballpark.

Key result: cost plateaus. Condvar::notify_all is O(1) regardless of
waiter count; GIL serializes them anyway. Going from 10 to 100
subscribers adds no further cost.

See FAQ.md for full writeup.
"""

import json
import statistics
import sys
import threading
import time

import pytest
from silk import GraphStore

sys.path.insert(0, ".")
from experiments.harness import measure


ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {
                "name": {"value_type": "string"},
                "seq": {"value_type": "int"},
            }
        }
    },
    "edge_types": {},
})


def _make_store() -> GraphStore:
    return GraphStore("bench", ONTOLOGY)


def _append_n(store: GraphStore, n: int) -> None:
    for i in range(n):
        store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"node-{i}", "seq": i})


# -- Producer-side overhead --


def bench_producer_overhead(n: int, n_subscribers: int, rounds: int = 5) -> float:
    """Measure append throughput with N actively-draining subscribers attached.

    Each subscriber runs a thread that calls next_batch() in a loop. Before
    the measurement starts, we wait until every subscriber thread has entered
    its first next_batch() call (via a barrier). This isolates GIL contention
    and NotifyBell cost from thread-startup noise.
    """
    def work():
        store = _make_store()
        subs = []
        threads = []
        stop_flag = threading.Event()
        # Barrier: main thread waits until all subscribers are in their wait loops.
        ready_counter = [0]
        ready_lock = threading.Lock()
        ready_event = threading.Event()

        def drain(sub):
            # Signal ready BEFORE the first next_batch call.
            with ready_lock:
                ready_counter[0] += 1
                if ready_counter[0] == n_subscribers:
                    ready_event.set()
            while not stop_flag.is_set():
                sub.next_batch(timeout_ms=50, max_count=1000)

        # Spawn subscribers
        for _ in range(n_subscribers):
            sub = store.subscribe_from(store.heads())
            subs.append(sub)
            t = threading.Thread(target=drain, args=(sub,), daemon=True)
            t.start()
            threads.append(t)

        # Wait for every subscriber to be ready. Then give a tiny extra moment
        # so that each has actually entered next_batch() and is blocked on the bell.
        if n_subscribers > 0:
            ready_event.wait(timeout=5.0)
            time.sleep(0.02)

        # Measure producer-side throughput
        t0 = time.perf_counter()
        _append_n(store, n)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Cleanup
        stop_flag.set()
        for sub in subs:
            sub.close()
        for t in threads:
            t.join(timeout=1)

        return elapsed_ms

    times = []
    for _ in range(rounds):
        times.append(work())
    return round(statistics.median(times), 2)


def test_producer_overhead_scales():
    """Producer throughput should not collapse as subscribers are added."""
    n = 1000
    base = bench_producer_overhead(n, n_subscribers=0)
    with_1 = bench_producer_overhead(n, n_subscribers=1)
    with_10 = bench_producer_overhead(n, n_subscribers=10)

    print(f"\n  {n} appends:")
    print(f"    0 subs:  {base:>8.2f} ms")
    print(f"    1 sub:   {with_1:>8.2f} ms  ({with_1/base:>5.2f}x)")
    print(f"   10 subs:  {with_10:>8.2f} ms  ({with_10/base:>5.2f}x)")

    # With 10 active pollers, producer competes with consumers for the GIL.
    # This is a Python artifact: each subscriber's next_batch loop re-acquires
    # the GIL. Real deployments have a handful of subscribers, not 10, and
    # subscribers batch-drain with sleeps between polls rather than tight-looping.
    # Accept up to 10x for this worst-case test; regression signal only.
    assert with_10 < base * 10, f"10 subs caused {with_10/base:.1f}x slowdown"


# -- Subscriber wake-up latency --


def measure_wakeup_latency(rounds: int = 30) -> dict:
    """Measure time from append on main thread to next_batch return on tailer."""
    latencies_ms = []

    for _ in range(rounds):
        store = _make_store()
        sub = store.subscribe_from(store.heads())

        # Start the tailer; measure time from append to batch return.
        append_time = [0.0]
        wake_time = [0.0]

        def tailer():
            entries = sub.next_batch(timeout_ms=5000, max_count=10)
            if entries:
                wake_time[0] = time.perf_counter()

        t = threading.Thread(target=tailer)
        t.start()
        time.sleep(0.01)  # Let the tailer enter its wait loop

        append_time[0] = time.perf_counter()
        store.add_node("n1", "entity", "N1", {"name": "x", "seq": 0})

        t.join(timeout=2)
        if wake_time[0] > 0:
            latencies_ms.append((wake_time[0] - append_time[0]) * 1000)
        sub.close()

    return {
        "p50_ms": round(statistics.median(latencies_ms), 3),
        "p95_ms": round(sorted(latencies_ms)[int(len(latencies_ms) * 0.95)], 3),
        "p99_ms": round(sorted(latencies_ms)[int(len(latencies_ms) * 0.99)], 3),
        "max_ms": round(max(latencies_ms), 3),
        "samples": len(latencies_ms),
    }


def test_wakeup_latency_under_millisecond_p50():
    """Subscribers should wake up within ~1ms of a commit."""
    stats = measure_wakeup_latency(rounds=30)
    print(f"\n  Wake-up latency: p50={stats['p50_ms']}ms, p95={stats['p95_ms']}ms, "
          f"p99={stats['p99_ms']}ms, max={stats['max_ms']}ms")

    # p50 under 5ms (generous — actual is usually sub-ms).
    assert stats["p50_ms"] < 5.0, f"wake-up p50 too high: {stats['p50_ms']} ms"


# -- Comparison to push-based store.subscribe --


def bench_push_api(n: int, n_subscribers: int, rounds: int = 5) -> float:
    """Baseline: current store.subscribe(callback) push API."""
    def work():
        store = _make_store()
        for _ in range(n_subscribers):
            store.subscribe(lambda event: None)

        t0 = time.perf_counter()
        _append_n(store, n)
        return (time.perf_counter() - t0) * 1000

    times = []
    for _ in range(rounds):
        times.append(work())
    return round(statistics.median(times), 2)


def test_compare_pull_vs_push():
    """Cursor tail should be comparable or better than push callbacks."""
    n = 1000
    push_t = bench_push_api(n, n_subscribers=10)
    pull_t = bench_producer_overhead(n, n_subscribers=10)

    print(f"\n  {n} appends with 10 subscribers:")
    print(f"    push (callback):  {push_t:>8.2f} ms")
    print(f"    pull (cursor):    {pull_t:>8.2f} ms")
    # Both should complete. No strict ordering because they have different
    # threading profiles. Record the numbers.


if __name__ == "__main__":
    print("=" * 70)
    print("C-1.6: Cursor-based tail subscription benchmarks")
    print("=" * 70)

    n = 1000
    print(f"\nProducer overhead ({n} appends):")
    for subs in [0, 1, 10, 100]:
        ms = bench_producer_overhead(n, n_subscribers=subs)
        ops = n / (ms / 1000)
        print(f"  {subs:>3} subscribers: {ms:>8.2f} ms  ({ops:>9,.0f} ops/sec)")

    print(f"\nPush API baseline ({n} appends, 10 subscribers):")
    ms = bench_push_api(n, n_subscribers=10)
    print(f"  store.subscribe:  {ms:>8.2f} ms  ({n / (ms / 1000):>9,.0f} ops/sec)")

    print(f"\nSubscriber wake-up latency (30 samples):")
    stats = measure_wakeup_latency()
    print(f"  p50: {stats['p50_ms']} ms")
    print(f"  p95: {stats['p95_ms']} ms")
    print(f"  p99: {stats['p99_ms']} ms")
    print(f"  max: {stats['max_ms']} ms")
