"""Sync payload compression — optional, user-configurable.

Compression is applied at the transport boundary, not inside the CRDT
engine. The Rust sync methods produce and consume raw bytes. This module
wraps those bytes with compress/decompress.

Built-in: ZlibCompression (default level=1 for best speed/ratio trade-off).
Custom: implement the SyncCompression protocol (compress + decompress methods).

Usage:
    from silk.compression import ZlibCompression

    comp = ZlibCompression()  # level=1 by default

    # Sender
    payload = store_a.receive_sync_offer(offer_bytes)
    compressed = comp.compress(payload)

    # Receiver
    payload = comp.decompress(compressed)
    store_b.merge_sync_payload(payload)
"""

from __future__ import annotations

import zlib
from typing import Protocol, runtime_checkable


@runtime_checkable
class SyncCompression(Protocol):
    """Protocol for sync payload compression.

    Implement compress() and decompress() to use a custom algorithm.
    Both must be pure functions: decompress(compress(data)) == data.
    """

    def compress(self, data: bytes) -> bytes: ...
    def decompress(self, data: bytes) -> bytes: ...


class NoCompression:
    """Pass-through — no compression. For benchmarking baselines."""

    def compress(self, data: bytes) -> bytes:
        return data

    def decompress(self, data: bytes) -> bytes:
        return data


class ZlibCompression:
    """zlib compression with configurable level.

    Level 1 (default): best speed/ratio trade-off for sync payloads.
    ~3x compression at ~1ms per 200KB payload.

    Level 6: Python's zlib default. Marginal size improvement, 3x slower.
    Level 9: Maximum compression. Negligible improvement over 6, 6x slower than 1.
    """

    def __init__(self, level: int = 1):
        if not 1 <= level <= 9:
            raise ValueError(f"zlib level must be 1-9, got {level}")
        self.level = level

    def compress(self, data: bytes) -> bytes:
        return zlib.compress(data, self.level)

    def decompress(self, data: bytes) -> bytes:
        return zlib.decompress(data)

    def __repr__(self) -> str:
        return f"ZlibCompression(level={self.level})"
