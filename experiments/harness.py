"""Silk experiment harness — lightweight measurement framework.

No external dependencies. Uses stdlib time, statistics, json, dataclasses.

Usage:
    from experiments.harness import measure, measure_sync_phase, print_table, to_json
"""

import json
import statistics
import time
from dataclasses import asdict, dataclass


@dataclass
class Stats:
    """Statistical summary of repeated measurements."""
    mean: float      # seconds
    median: float    # seconds
    min: float       # seconds
    max: float       # seconds
    stdev: float     # seconds
    rounds: int
    raw: list

    def mean_ms(self) -> float:
        return self.mean * 1000

    def median_ms(self) -> float:
        return self.median * 1000


@dataclass
class SyncMeasurement:
    """Measurement of one sync direction (A sends to B)."""
    offer_ms: float
    receive_ms: float
    merge_ms: float
    total_ms: float
    offer_bytes: int
    payload_bytes: int
    entries_sent: int


def measure(fn, *, rounds=10, warmup=2) -> Stats:
    """Run fn() repeatedly, return timing statistics.

    Args:
        fn: zero-argument callable to measure
        rounds: number of timed runs
        warmup: number of discarded warm-up runs
    """
    for _ in range(warmup):
        fn()

    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)

    return Stats(
        mean=statistics.mean(times),
        median=statistics.median(times),
        min=min(times),
        max=max(times),
        stdev=statistics.stdev(times) if len(times) >= 2 else 0.0,
        rounds=rounds,
        raw=times,
    )


def measure_sync_phase(store_a, store_b) -> SyncMeasurement:
    """Measure one sync direction: A sends entries to B.

    Measures offer generation, receive (entries_missing computation),
    and merge as separate phases. Runs once (not repeated — the caller
    handles repetition at the scenario level).
    """
    # Phase: B generates offer
    t0 = time.perf_counter()
    offer_bytes = store_b.generate_sync_offer()
    offer_ms = (time.perf_counter() - t0) * 1000

    # Phase: A computes what B is missing
    t0 = time.perf_counter()
    payload_bytes = store_a.receive_sync_offer(offer_bytes)
    receive_ms = (time.perf_counter() - t0) * 1000

    # Phase: B merges the payload
    t0 = time.perf_counter()
    merged = store_b.merge_sync_payload(payload_bytes)
    merge_ms = (time.perf_counter() - t0) * 1000

    return SyncMeasurement(
        offer_ms=offer_ms,
        receive_ms=receive_ms,
        merge_ms=merge_ms,
        total_ms=offer_ms + receive_ms + merge_ms,
        offer_bytes=len(offer_bytes),
        payload_bytes=len(payload_bytes),
        entries_sent=merged,
    )


def print_table(rows: list[dict], headers: list[str]) -> None:
    """Print a simple aligned text table."""
    widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(row.get(h, ""))))

    header_line = "  ".join(h.rjust(widths[h]) for h in headers)
    separator = "  ".join("-" * widths[h] for h in headers)
    print(header_line)
    print(separator)
    for row in rows:
        print("  ".join(str(row.get(h, "")).rjust(widths[h]) for h in headers))


def to_json(data, **kwargs) -> str:
    """Serialize to JSON string."""
    return json.dumps(data, indent=2, default=str, **kwargs)


def stats_dict(s: Stats) -> dict:
    """Convert Stats to a serializable dict with ms values."""
    return {
        "mean_ms": round(s.mean * 1000, 2),
        "median_ms": round(s.median * 1000, 2),
        "min_ms": round(s.min * 1000, 2),
        "max_ms": round(s.max * 1000, 2),
        "stdev_ms": round(s.stdev * 1000, 2),
        "rounds": s.rounds,
    }
