"""Notify strategies for cursor-based tail subscriptions (C-1.6).

Silk's `NotifyBell` (see silk.TailSubscription) fires on every successful
append and every sync merge. In hot-write workloads with active subscribers,
that per-append wake-up costs ~4µs of GIL contention (see
experiments/test_tail_breakdown.py). Coalescing reduces that dramatically
by skipping notifies within a configurable time window — subscribers wake
less often and drain bigger batches on each wake.

**Measured finding:** in typical workloads, coalescing does NOT measurably
reduce producer throughput. The observed active-subscriber overhead is
dominated by Python's GIL scheduling (`sys.setswitchinterval`, default
~5ms), not by the frequency of `notify_all()` calls. Immediate and
CoalescedNotify(1ms) both measure within ~2% of each other.

The API is kept for two reasons:
1. Workloads where subscribers do heavy per-wake processing can benefit
   from fewer, larger wakes.
2. Advanced users can implement domain-specific strategies by wrapping
   their logic in a custom `NotifyStrategy` object.

**Default is `ImmediateNotify`.** Lowest latency, no hidden coalescing
behavior. Switch to `CoalescedNotify(ms)` only if you've measured a
specific bottleneck.

Usage:

    from silk import GraphStore, ImmediateNotify, CoalescedNotify

    store = GraphStore("app", ONTOLOGY)

    # Default: immediate.
    # store.set_notify_strategy(ImmediateNotify())

    # Opt into coalescing for high-rate burst writes with heavy subscribers:
    store.set_notify_strategy(CoalescedNotify(min_interval_ms=5))

    # String shorthand:
    store.set_notify_strategy("coalesced", min_interval_ms=10)

Strategy objects are lightweight markers. The decision logic lives in Rust
for zero FFI cost on every append.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotifyStrategy(Protocol):
    """Protocol for tail-subscription notify strategies.

    Strategy objects carry a `__silk_strategy__` attribute naming the
    Rust-native variant to use, plus any parameters. The decision logic
    runs in Rust; Python only picks the variant.

    Built-in: ImmediateNotify, CoalescedNotify. See module docstring.
    """

    __silk_strategy__: str


class ImmediateNotify:
    """Fire notify on every successful append. Default. Lowest latency.

    Recommended when:
    - Standard workload. No specific measured bottleneck.
    - Writes are infrequent or interactive.
    - Every entry must be visible to subscribers within microseconds.
    - Subscribers drain cheaply (minimal per-entry processing).

    Tradeoff: with one active subscriber, each append pays ~4µs of GIL
    handoff. See experiments/test_tail_breakdown.py.
    """

    __silk_strategy__ = "immediate"

    def __repr__(self) -> str:
        return "ImmediateNotify()"


class CoalescedNotify:
    """Coalesce notify firings within a time window.

    The first notify in a quiet period fires immediately. Subsequent
    notifies within `min_interval_ms` are suppressed — the subscriber
    remains asleep and picks up all pending entries on its next wake.

    This is safe: the oplog is the buffer, and next_batch() always returns
    everything past the cursor. Coalescing only affects WHEN the wake
    happens, not WHETHER entries are delivered.

    Recommended when:
    - Subscribers do heavy per-wake processing (Python-side work that
      scales with the number of wakes, not entries).
    - You have measured a specific subscriber-side bottleneck.
    - You want wake frequency bounded for predictability.

    NOT a producer-throughput optimization in typical workloads — the
    active-subscriber overhead is dominated by Python's GIL scheduling,
    which coalescing does not change. See module docstring.

    Default: min_interval_ms=1. 0 is equivalent to ImmediateNotify.
    """

    __silk_strategy__ = "coalesced"

    def __init__(self, min_interval_ms: int = 1):
        if min_interval_ms < 0:
            raise ValueError("min_interval_ms must be >= 0")
        self.min_interval_ms = int(min_interval_ms)

    def __repr__(self) -> str:
        return f"CoalescedNotify(min_interval_ms={self.min_interval_ms})"
