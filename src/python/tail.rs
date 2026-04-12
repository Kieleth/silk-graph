//! Cursor-based tail subscriptions (C-1).
//!
//! Pull-with-cursor semantics: consumers hold a `Vec<Hash>` frontier, call
//! `next_batch(timeout_ms)` to get entries past their cursor. The oplog IS
//! the buffer — no per-subscriber in-memory queue. Slow subscribers just
//! lag, bounded only by oplog retention.
//!
//! Design: single `NotifyBell` per store. Producers (append, merge) tick a
//! monotonic counter and wake waiters via Condvar. Consumers check their
//! last-seen counter against the current one; if equal, they wait.
//!
//! Std-only, no tokio: `Arc<Mutex<u64> + Condvar>`.
//!
//! Thread-safety: PyTailSubscription uses interior mutability (Arc<Mutex>)
//! so `close()` can be called from one thread while `next_batch()` is
//! blocked on another. Without this, pyo3's refcell semantics would
//! raise "Already borrowed."

use pyo3::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use super::conversions::{entry_to_event_dict, parse_hex_hash};
use super::PyGraphStore;
use crate::entry::Hash;

/// Strategy for deciding when `bell.notify()` should actually wake waiters.
///
/// - `Immediate`: notify on every append (lowest latency, ~4µs overhead per
///   append when a subscriber is waiting due to GIL dance).
/// - `Coalesced { min_interval_ns }`: skip notifies within the given window
///   of the last one fired. Bursts of appends wake the subscriber at most
///   once per window; the subscriber drains everything on wake. Dramatically
///   reduces producer overhead for burst writes at the cost of up to
///   `min_interval` latency on quiet → busy transitions.
#[derive(Debug, Clone, Copy)]
pub enum NotifyStrategy {
    Immediate,
    Coalesced { min_interval_ns: u64 },
}

impl Default for NotifyStrategy {
    fn default() -> Self {
        // Immediate is the default. Benchmarks (experiments/test_tail_breakdown.py)
        // showed coalescing does NOT measurably improve producer throughput in
        // typical workloads — the observed "active subscriber" overhead is
        // dominated by Python's GIL scheduling (sys.setswitchinterval), not
        // by notify_all() frequency. The strategy API is kept for users with
        // specific subscriber-heavy workloads where coalescing may help, but
        // the default preserves lowest-latency semantics.
        NotifyStrategy::Immediate
    }
}

/// Producer→consumer wake-up primitive. Cheap when no one is waiting:
/// `notify()` decides via strategy, maybe locks the mutex briefly and
/// increments a counter, calls `notify_all()`. The decision is lock-free
/// (AtomicU64 compare-exchange for the coalesced case).
pub struct NotifyBell {
    /// Monotonic counter. Incremented on each notify. Consumers compare
    /// their last-seen value to detect new work without races.
    counter: Mutex<u64>,
    cvar: Condvar,
    /// Store-wide close flag — if true, next_batch returns immediately.
    closed: Mutex<bool>,
    /// Active strategy. Stored as Mutex<Strategy> so it can be changed at runtime.
    strategy: Mutex<NotifyStrategy>,
    /// For Coalesced strategy: nanoseconds since an arbitrary epoch at which
    /// the last notify fired. Compared against the current instant.
    last_notify_ns: AtomicU64,
    /// Clock epoch used with last_notify_ns. Set once at construction.
    epoch: Instant,
}

impl NotifyBell {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::with_strategy(NotifyStrategy::default()))
    }

    pub fn with_strategy(strategy: NotifyStrategy) -> Self {
        Self {
            counter: Mutex::new(0),
            cvar: Condvar::new(),
            closed: Mutex::new(false),
            strategy: Mutex::new(strategy),
            last_notify_ns: AtomicU64::new(0),
            epoch: Instant::now(),
        }
    }

    pub fn set_strategy(&self, strategy: NotifyStrategy) {
        *self.strategy.lock().unwrap() = strategy;
        // Reset last_notify_ns so the next notify fires immediately regardless
        // of the old strategy's state.
        self.last_notify_ns.store(0, Ordering::Relaxed);
    }

    /// Tick the counter and wake all waiters, subject to the strategy.
    pub fn notify(&self) {
        let should_fire = {
            let strategy = *self.strategy.lock().unwrap();
            match strategy {
                NotifyStrategy::Immediate => true,
                NotifyStrategy::Coalesced { min_interval_ns } => {
                    if min_interval_ns == 0 {
                        true
                    } else {
                        let now_ns = self.epoch.elapsed().as_nanos() as u64;
                        let last = self.last_notify_ns.load(Ordering::Relaxed);
                        if now_ns.saturating_sub(last) >= min_interval_ns {
                            // Try to claim this notify slot. CAS avoids double-fire
                            // under concurrent appends without a mutex.
                            self.last_notify_ns
                                .compare_exchange(
                                    last,
                                    now_ns,
                                    Ordering::Relaxed,
                                    Ordering::Relaxed,
                                )
                                .is_ok()
                        } else {
                            false
                        }
                    }
                }
            }
        };

        if should_fire {
            let mut guard = self.counter.lock().unwrap();
            *guard = guard.wrapping_add(1);
            self.cvar.notify_all();
        }
    }

    /// Current counter value. Caller uses this as "last seen."
    pub fn current(&self) -> u64 {
        *self.counter.lock().unwrap()
    }

    /// Wait until the counter differs from `last_seen`, or the timeout elapses.
    pub fn wait_until_changed(&self, last_seen: u64, timeout: Duration) {
        let guard = self.counter.lock().unwrap();
        let _ = self
            .cvar
            .wait_timeout_while(guard, timeout, |c| *c == last_seen)
            .unwrap();
    }

    pub fn close(&self) {
        *self.closed.lock().unwrap() = true;
        self.cvar.notify_all();
    }

    pub fn is_closed(&self) -> bool {
        *self.closed.lock().unwrap()
    }
}

impl Default for NotifyBell {
    fn default() -> Self {
        Self {
            counter: Mutex::new(0),
            cvar: Condvar::new(),
            closed: Mutex::new(false),
            strategy: Mutex::new(NotifyStrategy::default()),
            last_notify_ns: AtomicU64::new(0),
            epoch: Instant::now(),
        }
    }
}

// ---------------------------------------------------------------------------
// PyTailSubscription
// ---------------------------------------------------------------------------

/// Internal mutable state protected by a Mutex for cross-thread access.
struct TailInner {
    store: Py<PyGraphStore>,
    cursor: Vec<Hash>,
    closed: bool,
}

/// A cursor-based tail of the store's oplog.
///
/// Holds a frontier (`Vec<Hash>`), a `Arc<NotifyBell>`, and a `Py<PyGraphStore>`
/// for querying. `next_batch()` returns entries past the cursor, advancing the
/// cursor to the store's current heads on each call.
#[pyclass(name = "TailSubscription", module = "silk")]
pub struct PyTailSubscription {
    inner: Arc<Mutex<TailInner>>,
    bell: Arc<NotifyBell>,
}

impl PyTailSubscription {
    pub fn new(store: Py<PyGraphStore>, cursor: Vec<Hash>, bell: Arc<NotifyBell>) -> Self {
        Self {
            inner: Arc::new(Mutex::new(TailInner {
                store,
                cursor,
                closed: false,
            })),
            bell,
        }
    }
}

#[pymethods]
impl PyTailSubscription {
    /// Return the next batch of entries past the cursor.
    ///
    /// Blocks up to `timeout_ms` milliseconds if no entries are available.
    /// Returns at most `max_count` entries. Empty list on timeout (not an error).
    ///
    /// On success, the cursor advances to the store's current heads.
    ///
    /// Raises `ValueError` if the cursor contains hashes no longer in the oplog
    /// (e.g., compacted away).
    #[pyo3(signature = (timeout_ms=0, max_count=1000))]
    fn next_batch(
        &self,
        py: Python<'_>,
        timeout_ms: u64,
        max_count: usize,
    ) -> PyResult<Vec<PyObject>> {
        // First pass: try fetching without waiting.
        if self.is_closed() {
            return Ok(vec![]);
        }

        let last_seen = self.bell.current();
        if let Some(entries) = self.try_fetch(py, max_count)? {
            return Ok(entries);
        }

        // No entries available. Release GIL and wait on the bell.
        if timeout_ms == 0 {
            return Ok(vec![]);
        }

        let bell = Arc::clone(&self.bell);
        py.allow_threads(move || {
            bell.wait_until_changed(last_seen, Duration::from_millis(timeout_ms));
        });

        // After waking, try once more. No further waiting.
        if self.is_closed() || self.bell.is_closed() {
            return Ok(vec![]);
        }
        Ok(self.try_fetch(py, max_count)?.unwrap_or_default())
    }

    /// Return the current cursor as a list of hex-encoded hashes.
    /// Persist this to resume after restart.
    fn current_cursor(&self) -> Vec<String> {
        self.inner
            .lock()
            .unwrap()
            .cursor
            .iter()
            .map(hex::encode)
            .collect()
    }

    /// Close the subscription. Subsequent `next_batch` calls return empty.
    /// Safe to call while `next_batch` is blocked on another thread.
    fn close(&self) {
        {
            let mut guard = self.inner.lock().unwrap();
            guard.closed = true;
        }
        // Wake any blocked next_batch so it observes the closed flag.
        self.bell.notify();
    }

    fn __repr__(&self) -> String {
        let guard = self.inner.lock().unwrap();
        format!(
            "TailSubscription(cursor={} heads, closed={})",
            guard.cursor.len(),
            guard.closed
        )
    }
}

impl PyTailSubscription {
    fn is_closed(&self) -> bool {
        self.inner.lock().unwrap().closed
    }

    /// Query the oplog for entries past the cursor. Returns Some(entries) if
    /// non-empty, None if no new entries (caller may wait). Advances cursor
    /// to the store's current heads on non-empty return.
    fn try_fetch(&self, py: Python<'_>, max_count: usize) -> PyResult<Option<Vec<PyObject>>> {
        // Take a snapshot of the cursor (release mutex before querying the
        // store, which also requires the GIL / RefCell).
        let cursor_snapshot = {
            let guard = self.inner.lock().unwrap();
            guard.cursor.clone()
        };

        // Borrow the store to query the oplog.
        let (py_entries, new_cursor) = {
            let store = {
                let guard = self.inner.lock().unwrap();
                guard.store.clone_ref(py)
            };
            let borrowed = store.borrow(py);
            let oplog = borrowed.backend_oplog();

            let entries = oplog.entries_since_heads(&cursor_snapshot).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("stale cursor: {e}"))
            })?;

            if entries.is_empty() {
                return Ok(None);
            }

            // Advance cursor to store's current heads (the frontier after this batch),
            // unless the batch was truncated by max_count.
            let truncated = entries.len() > max_count;
            let new_cursor = if truncated {
                vec![entries[max_count - 1].hash]
            } else {
                oplog.heads()
            };

            // Tail subscriptions serve entries that arrived either locally or
            // via sync. We don't distinguish here (is_local=false is the safer
            // default since subscribers often process events the same way).
            let py_entries: Vec<PyObject> = entries
                .iter()
                .take(max_count)
                .map(|e| entry_to_event_dict(py, e, false))
                .collect::<PyResult<Vec<_>>>()?;

            (py_entries, new_cursor)
        };

        // Advance cursor.
        {
            let mut guard = self.inner.lock().unwrap();
            guard.cursor = new_cursor;
        }

        Ok(Some(py_entries))
    }
}

// ---------------------------------------------------------------------------
// Helper for PyGraphStore::subscribe_from
// ---------------------------------------------------------------------------

/// Parse a Python list of hex hashes into Vec<Hash>. Validates format but
/// NOT existence in the oplog (the subscription handles that on first fetch).
pub fn parse_cursor(cursor: Vec<String>) -> PyResult<Vec<Hash>> {
    cursor
        .iter()
        .map(|s| parse_hex_hash(s))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid cursor: {e}")))
}
