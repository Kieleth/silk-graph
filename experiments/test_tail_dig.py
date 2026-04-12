"""Dig into C-1 overhead. Find the actual bottleneck.

Measurements:
  A. Pure producer (no subscription object at all)
  B. With subscription object, no drain thread
  C. With drain thread that NEVER calls next_batch (idle)
  D. With drain thread calling next_batch (active)
  E. Per-call next_batch cost (entries available, no wait)
  F. Per-call next_batch cost (empty, short wait, timeout)

Goal: decompose the 4µs active-subscriber overhead into:
  (a) notify_all cost with active waiter
  (b) OS thread wake
  (c) GIL handoff
  (d) Subscriber's per-call Rust work
  (e) Subscriber's Python-side loop overhead
"""

import json
import statistics
import sys
import threading
import time

from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {"entity": {"properties": {"name": {"value_type": "string"}}}},
    "edge_types": {},
})


def _t():
    return time.perf_counter_ns()


def _median_ns(times):
    return int(statistics.median(times))


def measure_per_call(fn, n, rounds=5):
    times = []
    for _ in range(rounds):
        t0 = _t()
        fn()
        elapsed = _t() - t0
        times.append(elapsed / n)
    return _median_ns(times)


# A: pure producer
def bench_A(n):
    def work():
        store = GraphStore("b", ONTOLOGY)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
    return measure_per_call(work, n)


# B: subscription object exists but no thread, no one waiting
def bench_B(n):
    def work():
        store = GraphStore("b", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
        sub.close()
    return measure_per_call(work, n)


# C: drain thread exists but DOESN'T call next_batch — truly blocked
def bench_C(n):
    def work():
        store = GraphStore("b", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        stop = threading.Event()
        # Thread that is fully blocked on a condition variable with long timeout.
        # Only wakes when explicitly signaled.
        def idle():
            stop.wait()  # blocks until set(), no periodic wake
        t = threading.Thread(target=idle, daemon=True)
        t.start()
        time.sleep(0.02)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
        stop.set()
        sub.close()
        t.join(timeout=1)
    return measure_per_call(work, n)


# C': drain thread blocked in a Rust call (on the bell cvar) via next_batch with huge timeout
def bench_C_rust_blocked(n):
    def work():
        store = GraphStore("b", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        stop = threading.Event()
        ready = threading.Event()
        # Thread that issues ONE next_batch call with huge timeout, then exits.
        # During the producer burst, the thread is blocked in Rust (cvar wait),
        # NOT in Python. Should reveal whether GIL overhead comes from "thread
        # exists" or specifically from "thread wakes periodically in Python".
        def idle():
            ready.set()
            sub.next_batch(timeout_ms=3600_000, max_count=10000)  # 1h timeout
        t = threading.Thread(target=idle, daemon=True)
        t.start()
        ready.wait(timeout=2)
        time.sleep(0.02)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
        sub.close()  # wakes the thread
        t.join(timeout=2)
    return measure_per_call(work, n)


# D: drain thread calling next_batch actively
def bench_D(n):
    def work():
        store = GraphStore("b", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        stop = threading.Event()
        ready = threading.Event()
        def drain():
            ready.set()
            while not stop.is_set():
                sub.next_batch(timeout_ms=50, max_count=10000)
        t = threading.Thread(target=drain, daemon=True)
        t.start()
        ready.wait(timeout=2)
        time.sleep(0.02)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
        stop.set()
        sub.close()
        t.join(timeout=1)
    return measure_per_call(work, n)


# E: per-call next_batch cost when entries are available
def bench_next_batch_with_entries():
    """Measure pure Rust cost of next_batch when entries are available."""
    store = GraphStore("b", ONTOLOGY)
    for i in range(1000):
        store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})

    # Each call: cursor is fresh, entries available, no wait.
    times = []
    rounds = 100
    for _ in range(rounds):
        sub = store.subscribe_from([])
        t0 = _t()
        entries = sub.next_batch(timeout_ms=0, max_count=1000)
        elapsed = _t() - t0
        times.append(elapsed / len(entries) if entries else elapsed)
        sub.close()

    return _median_ns(times)


# F: per-call next_batch cost when NO entries (short timeout)
def bench_next_batch_empty():
    store = GraphStore("b", ONTOLOGY)
    sub = store.subscribe_from(store.heads())
    times = []
    rounds = 30
    for _ in range(rounds):
        t0 = _t()
        sub.next_batch(timeout_ms=5, max_count=10)
        elapsed = _t() - t0
        times.append(elapsed)
    sub.close()
    return _median_ns(times)


# P: polling pattern — same thread as producer, interleaved
def bench_P_polling(n):
    """Producer also drains via non-blocking next_batch. Zero threads."""
    def work():
        store = GraphStore("b", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        # Drain every 100 appends.
        poll_every = 100
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
            if (i + 1) % poll_every == 0:
                sub.next_batch(timeout_ms=0, max_count=poll_every)
        sub.next_batch(timeout_ms=0, max_count=n)  # final drain
        sub.close()
    return measure_per_call(work, n)


if __name__ == "__main__":
    n = 10_000
    print("=" * 70)
    print(f"C-1 deep dig — where does the overhead really go? ({n} appends)")
    print("=" * 70)

    a = bench_A(n)
    b = bench_B(n)
    c = bench_C(n)
    c_rust = bench_C_rust_blocked(n)
    d = bench_D(n)
    p = bench_P_polling(n)

    print(f"\n  A. Pure producer:                         {a:>6} ns/append (100%)")
    print(f"  B. + subscription obj (no thread):        {b:>6} ns/append ({b/a*100:.1f}%)")
    print(f"  P. + POLLING pattern (same thread):       {p:>6} ns/append ({p/a*100:.1f}%)")
    print(f"  C. + idle thread (blocked on Event):      {c:>6} ns/append ({c/a*100:.1f}%)")
    print(f"  C'. + thread blocked IN RUST on cvar:     {c_rust:>6} ns/append ({c_rust/a*100:.1f}%)")
    print(f"  D. + draining thread (active):            {d:>6} ns/append ({d/a*100:.1f}%)")

    print()
    e = bench_next_batch_with_entries()
    f = bench_next_batch_empty()
    print(f"  E. next_batch per entry (with data):  {e:>6} ns/entry")
    print(f"  F. next_batch empty + 5ms wait:       {f:>6} ns total (~5ms expected)")

    print()
    print(f"  Overhead of active subscriber: {d - a} ns per producer append")
