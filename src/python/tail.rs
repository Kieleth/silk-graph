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
//! Std-only, no external async runtime: `Arc<Mutex<u64> + Condvar>`.
//!
//! Thread-safety: PyTailSubscription uses lock-free atomics for the closed
//! flag and a dedicated cursor mutex (separated from other state) so
//! `close()` can be called from one thread while `next_batch()` is blocked
//! on another.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use super::conversions::{entry_to_event_dict, parse_hex_hash};
use super::PyGraphStore;
use crate::entry::Hash;

/// Producer→consumer wake-up primitive. Cheap when no one is waiting:
/// `notify()` locks the mutex briefly and increments a counter, calls
/// `notify_all()` on an empty waiter list (essentially a no-op).
pub struct NotifyBell {
    /// Monotonic counter. Incremented on each notify. Consumers compare
    /// their last-seen value to detect new work without races.
    counter: Mutex<u64>,
    cvar: Condvar,
    /// Lock-free close flag — hot path uses atomic load instead of mutex.
    closed: AtomicBool,
}

impl NotifyBell {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// Tick the counter and wake all waiters. Call after any successful
    /// append or merge. Essentially free when no waiters are registered.
    pub fn notify(&self) {
        let mut guard = self.counter.lock().unwrap();
        *guard = guard.wrapping_add(1);
        self.cvar.notify_all();
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
        self.closed.store(true, Ordering::Release);
        self.cvar.notify_all();
    }

    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::Acquire)
    }
}

impl Default for NotifyBell {
    fn default() -> Self {
        Self {
            counter: Mutex::new(0),
            cvar: Condvar::new(),
            closed: AtomicBool::new(false),
        }
    }
}

// ---------------------------------------------------------------------------
// PyTailSubscription
// ---------------------------------------------------------------------------

/// A cursor-based tail of the store's oplog.
///
/// State is split to minimize lock contention:
/// - `store` and `bell` are immutable Arc-ish references, no locking needed.
/// - `closed` is an AtomicBool for lock-free hot-path checks.
/// - `cursor` is a dedicated Mutex only held briefly per next_batch.
#[pyclass(name = "TailSubscription", module = "silk")]
pub struct PyTailSubscription {
    store: Py<PyGraphStore>,
    cursor: Mutex<Vec<Hash>>,
    closed: AtomicBool,
    bell: Arc<NotifyBell>,
}

impl PyTailSubscription {
    pub fn new(store: Py<PyGraphStore>, cursor: Vec<Hash>, bell: Arc<NotifyBell>) -> Self {
        Self {
            store,
            cursor: Mutex::new(cursor),
            closed: AtomicBool::new(false),
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
        // Fast path: lock-free close check.
        if self.closed.load(Ordering::Acquire) {
            return Ok(vec![]);
        }

        let last_seen = self.bell.current();
        if let Some(entries) = self.try_fetch(py, max_count)? {
            return Ok(entries);
        }

        if timeout_ms == 0 {
            return Ok(vec![]);
        }

        // Release GIL and wait.
        let bell = Arc::clone(&self.bell);
        py.allow_threads(move || {
            bell.wait_until_changed(last_seen, Duration::from_millis(timeout_ms));
        });

        if self.closed.load(Ordering::Acquire) || self.bell.is_closed() {
            return Ok(vec![]);
        }
        Ok(self.try_fetch(py, max_count)?.unwrap_or_default())
    }

    /// Return the current cursor as a list of hex-encoded hashes.
    fn current_cursor(&self) -> Vec<String> {
        self.cursor
            .lock()
            .unwrap()
            .iter()
            .map(hex::encode)
            .collect()
    }

    /// Close the subscription. Subsequent `next_batch` calls return empty.
    /// Safe to call from any thread, concurrent with `next_batch`.
    fn close(&self) {
        self.closed.store(true, Ordering::Release);
        // Wake any blocked next_batch so it observes the closed flag.
        self.bell.notify();
    }

    fn __repr__(&self) -> String {
        let heads = self.cursor.lock().unwrap().len();
        format!(
            "TailSubscription(cursor={} heads, closed={})",
            heads,
            self.closed.load(Ordering::Acquire)
        )
    }
}

impl PyTailSubscription {
    /// Query the oplog for entries past the cursor. Returns Some(entries) if
    /// non-empty, None if no new entries. Advances cursor on non-empty return.
    fn try_fetch(&self, py: Python<'_>, max_count: usize) -> PyResult<Option<Vec<PyObject>>> {
        // Single mutex acquire for the cursor snapshot.
        let cursor_snapshot = {
            let guard = self.cursor.lock().unwrap();
            guard.clone()
        };

        // Borrow the store (requires GIL — we already hold it).
        let borrowed = self.store.bind(py).borrow();
        let oplog = borrowed.backend_oplog();

        let entries = oplog
            .entries_since_heads(&cursor_snapshot)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("stale cursor: {e}")))?;

        if entries.is_empty() {
            return Ok(None);
        }

        // Compute new cursor before releasing the borrow.
        let truncated = entries.len() > max_count;
        let new_cursor = if truncated {
            vec![entries[max_count - 1].hash]
        } else {
            oplog.heads()
        };

        // Convert entries to Python dicts.
        let py_entries: Vec<PyObject> = entries
            .iter()
            .take(max_count)
            .map(|e| entry_to_event_dict(py, e, false))
            .collect::<PyResult<Vec<_>>>()?;

        // Single mutex acquire to update cursor.
        {
            let mut guard = self.cursor.lock().unwrap();
            *guard = new_cursor;
        }

        Ok(Some(py_entries))
    }
}

// ---------------------------------------------------------------------------
// Helper for PyGraphStore::subscribe_from
// ---------------------------------------------------------------------------

/// Parse a Python list of hex hashes into Vec<Hash>.
pub fn parse_cursor(cursor: Vec<String>) -> PyResult<Vec<Hash>> {
    cursor
        .iter()
        .map(|s| parse_hex_hash(s))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid cursor: {e}")))
}
