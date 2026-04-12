"""C-1 overhead breakdown: where exactly does the time go?

Isolates:
  1. Pure Rust notify cost (Condvar::notify_all on empty waiters).
  2. Per-append cost WITHOUT any notify (raw add_node).
  3. Per-append cost WITH notify, zero subscribers.
  4. Per-append cost WITH notify, one active subscriber.

Answers: is the overhead in the Rust notify primitive, or in the
GIL dance when a subscriber wakes up and re-enters Python?
"""

import json
import statistics
import threading
import time

from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {"entity": {"properties": {"name": {"value_type": "string"}}}},
    "edge_types": {},
})


def _bench_loop(fn, n=10000, rounds=10):
    """Return median wall time in ns per call."""
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter_ns()
        fn()
        elapsed_ns = time.perf_counter_ns() - t0
        times.append(elapsed_ns / n)
    return int(statistics.median(times))


def bench_add_node_no_sub(n):
    def work():
        store = GraphStore("bench", ONTOLOGY)
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
    return _bench_loop(work, n=n, rounds=5)


def bench_add_node_with_idle_sub(n):
    """Subscriber exists but NOT calling next_batch — no waiter registered."""
    def work():
        store = GraphStore("bench", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})
        sub.close()
    return _bench_loop(work, n=n, rounds=5)


def bench_add_node_with_active_sub(n):
    """One subscriber thread actively calling next_batch."""
    def work():
        store = GraphStore("bench", ONTOLOGY)
        sub = store.subscribe_from(store.heads())
        stop = threading.Event()
        ready = threading.Event()

        def drain():
            ready.set()
            while not stop.is_set():
                sub.next_batch(timeout_ms=50, max_count=1000)

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        ready.wait(timeout=2)
        time.sleep(0.02)

        for i in range(n):
            store.add_node(f"n-{i}", "entity", f"N{i}", {"name": f"x-{i}"})

        stop.set()
        sub.close()
        t.join(timeout=1)
    return _bench_loop(work, n=n, rounds=5)


def bench_add_node_with_active_sub_immediate(n):
    """Active subscriber + ImmediateNotify (every append wakes)."""
    def work():
        store = GraphStore("bench", ONTOLOGY)
        store.set_notify_strategy("immediate")
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
    return _bench_loop(work, n=n, rounds=5)


def bench_add_node_with_active_sub_coalesced(n, ms):
    """Active subscriber + CoalescedNotify(ms)."""
    def work():
        store = GraphStore("bench", ONTOLOGY)
        store.set_notify_strategy("coalesced", min_interval_ms=ms)
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
    return _bench_loop(work, n=n, rounds=5)


if __name__ == "__main__":
    n = 10_000

    print("=" * 70)
    print(f"C-1 overhead breakdown ({n} appends per round, 5 rounds, median)")
    print("=" * 70)

    no_sub = bench_add_node_no_sub(n)
    idle_sub = bench_add_node_with_idle_sub(n)
    active_sub = bench_add_node_with_active_sub(n)

    print(f"\n  add_node, no subscribers:            {no_sub:>6} ns/op  (100%)")
    print(f"  add_node, idle subscriber (no wait): {idle_sub:>6} ns/op  ({idle_sub/no_sub*100:>5.1f}%)")
    print(f"  add_node, active subscriber (default): {active_sub:>6} ns/op  ({active_sub/no_sub*100:>5.1f}%)")

    print()
    print("=" * 70)
    print("Notify strategy comparison (active subscriber, burst writes)")
    print("=" * 70)

    imm = bench_add_node_with_active_sub_immediate(n)
    c1 = bench_add_node_with_active_sub_coalesced(n, 1)
    c5 = bench_add_node_with_active_sub_coalesced(n, 5)

    print(f"\n  ImmediateNotify:         {imm:>6} ns/op  ({imm/no_sub*100:>5.1f}% of baseline)")
    print(f"  CoalescedNotify(1ms):    {c1:>6} ns/op  ({c1/no_sub*100:>5.1f}% of baseline)")
    print(f"  CoalescedNotify(5ms):    {c5:>6} ns/op  ({c5/no_sub*100:>5.1f}% of baseline)")
    print()
    print(f"  Coalesce(1ms) vs Immediate: {(imm - c1) / imm * 100:.1f}% faster")
