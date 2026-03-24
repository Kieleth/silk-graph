"""Compaction policies — when to compact the oplog.

Silk provides the compaction primitive (`store.compact()`). This module
provides policies that decide WHEN to compact. Two built-in policies
ship with Silk. Custom policies implement the `CompactionPolicy` protocol.

Usage:
    from silk.compaction import IntervalPolicy, ThresholdPolicy

    # Compact every hour
    policy = IntervalPolicy(seconds=3600)
    policy.check(store)  # call periodically

    # Compact when oplog exceeds 1000 entries
    policy = ThresholdPolicy(max_entries=1000)
    policy.check(store)  # call after writes

    # Custom policy
    class MyPolicy:
        def should_compact(self, store) -> bool:
            return store.len() > 5000

        def check(self, store):
            if self.should_compact(store):
                store.compact()

Safety note: compact() should only be called when all peers have synced
to the current state. The built-in policies don't know about peers —
the application is responsible for safety in multi-peer deployments.
For single-instance stores (no sync), compaction is always safe.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CompactionPolicy(Protocol):
    """Extension point for custom compaction policies.

    Implement should_compact() and check(). Or just check() if you
    want full control over the compaction decision + execution.
    """

    def should_compact(self, store: Any) -> bool:
        """Return True if the store should be compacted now."""
        ...

    def check(self, store: Any) -> str | None:
        """Check the policy and compact if needed.

        Returns the checkpoint hash if compaction happened, None otherwise.
        """
        ...


class IntervalPolicy:
    """Compact at most once every N seconds.

    Call `check(store)` periodically (e.g., after each sync round,
    or on a timer). Compaction only happens if enough time has passed
    since the last compaction.

    Args:
        seconds: Minimum interval between compactions.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last_compact: float = 0.0

    def should_compact(self, store: Any) -> bool:
        return (time.time() - self._last_compact) >= self.seconds

    def check(self, store: Any) -> str | None:
        if self.should_compact(store):
            h = store.compact()
            self._last_compact = time.time()
            return h
        return None


class ThresholdPolicy:
    """Compact when the oplog exceeds a threshold.

    Call `check(store)` after writes or batches. Compaction happens
    when the oplog entry count exceeds the threshold.

    Args:
        max_entries: Compact when store.len() exceeds this.
    """

    def __init__(self, max_entries: int = 1000):
        self.max_entries = max_entries

    def should_compact(self, store: Any) -> bool:
        return store.len() > self.max_entries

    def check(self, store: Any) -> str | None:
        if self.should_compact(store):
            return store.compact()
        return None
