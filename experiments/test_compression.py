"""EXP-05: Sync payload compression — cost vs benefit.

Measures the trade-off between bandwidth reduction and CPU overhead
for compressed sync payloads. Tests built-in ZlibCompression at
multiple levels and validates the SyncCompression protocol.

Usage:
    python experiments/test_compression.py
    pytest experiments/test_compression.py -v
"""

import statistics
import sys
import time

import pytest

from silk import GraphStore
from silk.compression import NoCompression, SyncCompression, ZlibCompression

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


def _build_scenario(n: int):
    """Two stores: A has n entities, B is empty."""
    a = GraphStore("peer-a", ONTOLOGY)
    for i in range(n):
        a.add_node(f"n-{i}", "entity", f"Node {i}", {
            "name": f"entity-{i}",
            "status": "active",
            "seq": i,
        })
    b = GraphStore("peer-b", ONTOLOGY)
    return a, b


def measure_compression(n: int, comp: SyncCompression, rounds: int = 20) -> dict:
    """Measure sync with compression applied at the transport boundary."""
    a, b = _build_scenario(n)

    # Generate raw payload once for size measurement
    offer = b.generate_sync_offer()
    raw_payload = a.receive_sync_offer(offer)
    compressed = comp.compress(raw_payload)

    # Round-trip correctness
    assert comp.decompress(compressed) == raw_payload, "round-trip failed"

    # Measure full sync cycle with compression
    times = []
    for _ in range(rounds):
        a_fresh, b_fresh = _build_scenario(n)
        t0 = time.perf_counter()
        offer = b_fresh.generate_sync_offer()
        payload = a_fresh.receive_sync_offer(offer)
        compressed_payload = comp.compress(payload)
        decompressed = comp.decompress(compressed_payload)
        b_fresh.merge_sync_payload(decompressed)
        times.append((time.perf_counter() - t0) * 1000)

    return {
        "compressor": repr(comp),
        "N": n,
        "raw_bytes": len(raw_payload),
        "compressed_bytes": len(compressed),
        "ratio": round(len(compressed) / len(raw_payload) * 100, 1),
        "savings": round((1 - len(compressed) / len(raw_payload)) * 100, 1),
        "sync_ms": round(statistics.median(times), 2),
    }


# ---------------------------------------------------------------------------
# Metric thresholds
# ---------------------------------------------------------------------------

ZLIB1_MAX_RATIO = 40.0     # zlib-1 should achieve at least 60% reduction
COMPRESSION_MAX_OVERHEAD_RATIO = 3.0  # compressed sync should be < 3x raw sync time


def test_compression_round_trip():
    """All compression implementations must round-trip correctly."""
    a, b = _build_scenario(100)
    offer = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer)

    for comp in [NoCompression(), ZlibCompression(1), ZlibCompression(6), ZlibCompression(9)]:
        compressed = comp.compress(payload)
        assert comp.decompress(compressed) == payload, f"{comp} round-trip failed"


def test_compression_reduces_size():
    """ZlibCompression must reduce payload size."""
    a, b = _build_scenario(500)
    offer = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer)

    comp = ZlibCompression(level=1)
    compressed = comp.compress(payload)

    check_metrics([
        Metric(
            name="zlib1_compression_ratio",
            measured=round(len(compressed) / len(payload) * 100, 1),
            threshold=ZLIB1_MAX_RATIO,
            op="<",
            unit="%",
        ),
    ], label="EXP-05 compression ratio")


def test_compression_overhead_bounded():
    """Compressed sync should not be more than 3x slower than raw sync."""
    n = 500
    raw = measure_compression(n, NoCompression(), rounds=10)
    compressed = measure_compression(n, ZlibCompression(1), rounds=10)

    ratio = compressed["sync_ms"] / raw["sync_ms"] if raw["sync_ms"] > 0 else float("inf")

    check_metrics([
        Metric(
            name="compression_overhead_ratio",
            measured=round(ratio, 2),
            threshold=COMPRESSION_MAX_OVERHEAD_RATIO,
            op="<",
            unit="x",
        ),
    ], label="EXP-05 compression overhead")


def test_custom_compression_protocol():
    """User-defined compression implementing SyncCompression protocol works."""
    class Rot13Compression:
        """Silly compressor for testing the protocol."""
        def compress(self, data: bytes) -> bytes:
            return bytes((b + 13) % 256 for b in data)
        def decompress(self, data: bytes) -> bytes:
            return bytes((b - 13) % 256 for b in data)

    comp = Rot13Compression()
    assert isinstance(comp, SyncCompression), "must satisfy protocol"

    a, b = _build_scenario(50)
    offer = b.generate_sync_offer()
    payload = a.receive_sync_offer(offer)

    compressed = comp.compress(payload)
    assert comp.decompress(compressed) == payload


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import platform
    print(f"EXP-05: Sync Payload Compression")
    print(f"  platform: {platform.machine()} / {platform.system()}")
    print(f"  python: {platform.python_version()}")
    print()

    compressors = [
        NoCompression(),
        ZlibCompression(1),
        ZlibCompression(6),
        ZlibCompression(9),
    ]

    scales = [100, 500, 1000]

    results = []
    for n in scales:
        for comp in compressors:
            print(f"  {n} entities, {comp!r}...", end=" ", flush=True)
            r = measure_compression(n, comp)
            print(f"{r['sync_ms']}ms, {r['ratio']}% of raw")
            results.append(r)

    print()
    print_table(results, ["compressor", "N", "raw_bytes", "compressed_bytes", "ratio", "savings", "sync_ms"])
