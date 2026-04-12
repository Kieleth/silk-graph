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

use pyo3::prelude::*;
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use super::conversions::{entry_to_pydict, parse_hex_hash};
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
    /// Permanent close flag — if true, next_batch returns immediately.
    closed: Mutex<bool>,
}

impl NotifyBell {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            counter: Mutex::new(0),
            cvar: Condvar::new(),
            closed: Mutex::new(false),
        })
    }

    /// Tick the counter and wake all waiters. Call after any successful
    /// append or merge. ~100ns when no waiters.
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
    /// Returns the new counter value.
    pub fn wait_until_changed(&self, last_seen: u64, timeout: Duration) -> u64 {
        let guard = self.counter.lock().unwrap();
        let (new_guard, _) = self
            .cvar
            .wait_timeout_while(guard, timeout, |c| *c == last_seen && !self.is_closed())
            .unwrap();
        *new_guard
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
        }
    }
}

// ---------------------------------------------------------------------------
// PyTailSubscription
// ---------------------------------------------------------------------------

/// A cursor-based tail of the store's oplog.
///
/// Holds a frontier (`Vec<Hash>`), an `Arc<NotifyBell>`, and a `Py<PyGraphStore>`
/// for querying. `next_batch()` returns entries past the cursor, advancing the
/// cursor to the store's current heads on each call.
#[pyclass(name = "TailSubscription", module = "silk")]
pub struct PyTailSubscription {
    store: Py<PyGraphStore>,
    cursor: Vec<Hash>,
    bell: Arc<NotifyBell>,
    /// Per-subscription close flag (separate from bell's store-wide close).
    closed: bool,
}

impl PyTailSubscription {
    pub fn new(store: Py<PyGraphStore>, cursor: Vec<Hash>, bell: Arc<NotifyBell>) -> Self {
        Self {
            store,
            cursor,
            bell,
            closed: false,
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
        &mut self,
        py: Python<'_>,
        timeout_ms: u64,
        max_count: usize,
    ) -> PyResult<Vec<PyObject>> {
        if self.closed {
            return Ok(vec![]);
        }

        // First pass: try without waiting.
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

        // After waking, try once more. No further waiting — if still empty,
        // return empty. Caller loops.
        if self.closed || self.bell.is_closed() {
            return Ok(vec![]);
        }
        Ok(self.try_fetch(py, max_count)?.unwrap_or_default())
    }

    /// Return the current cursor as a list of hex-encoded hashes.
    /// Persist this to resume after restart.
    fn current_cursor(&self) -> Vec<String> {
        self.cursor.iter().map(hex::encode).collect()
    }

    /// Close the subscription. Subsequent `next_batch` calls return empty.
    fn close(&mut self) {
        self.closed = true;
    }

    fn __repr__(&self) -> String {
        format!(
            "TailSubscription(cursor={} heads, closed={})",
            self.cursor.len(),
            self.closed
        )
    }
}

impl PyTailSubscription {
    /// Query the oplog for entries past the cursor. Returns Some(entries) if
    /// non-empty, None if no new entries (caller may wait). Advances cursor
    /// to the store's current heads on non-empty return.
    fn try_fetch(&mut self, py: Python<'_>, max_count: usize) -> PyResult<Option<Vec<PyObject>>> {
        let store = self.store.borrow(py);
        let oplog = store.backend_oplog();

        let entries = oplog
            .entries_since_heads(&self.cursor)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("stale cursor: {e}")))?;

        if entries.is_empty() {
            return Ok(None);
        }

        // Advance cursor to store's current heads (the frontier after this batch).
        let new_heads = oplog.heads();
        self.cursor = new_heads;

        // Cap batch size and convert to Python dicts.
        let py_entries: Vec<PyObject> = entries
            .iter()
            .take(max_count)
            .map(|e| entry_to_pydict(py, e))
            .collect::<PyResult<Vec<_>>>()?;

        // If we truncated, we need to leave the cursor BEFORE the cutoff so
        // the next call picks up the rest. We do this by NOT advancing past
        // entries we didn't return. Simplest: if truncated, advance cursor
        // only to the last returned entry's parents (frontier of what we sent).
        if entries.len() > max_count {
            let last_hash = entries[max_count - 1].hash;
            self.cursor = vec![last_hash];
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
