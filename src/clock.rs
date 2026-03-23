use serde::{Deserialize, Serialize};
use std::time::SystemTime;

/// Hybrid Logical Clock for causal + real-time ordering across distributed nodes.
///
/// R-01: Combines wall-clock time (physical_ms) with a logical counter.
/// The physical component captures *when* an event happened in real time.
/// The logical component orders events that happen within the same millisecond.
///
/// Based on Kulkarni, Demirbas, Madeppa, Avva & Leone (2014).
/// Used in production by CockroachDB for MVCC timestamps.
///
/// Rules:
/// - On local event: physical = max(old_physical, wall_clock).
///   If physical advanced → logical = 0. Else → logical += 1.
/// - On merge: physical = max(local, remote, wall_clock).
///   Logical follows the same advancement/increment rule.
/// - Total order: (physical, logical, id). Higher physical wins.
///   Same physical → higher logical wins. Both equal → lower id wins.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HybridClock {
    /// Unique identifier of the instance that owns this clock
    pub id: String,
    /// Physical time in milliseconds since Unix epoch
    pub physical_ms: u64,
    /// Logical counter — incremented when physical time doesn't advance
    pub logical: u32,
}

/// Get current wall-clock time in milliseconds since Unix epoch.
fn current_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

impl HybridClock {
    /// Create a new clock with the current wall-clock time.
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            physical_ms: current_time_ms(),
            logical: 0,
        }
    }

    /// Create a clock with explicit values (for tests and deserialization).
    pub fn with_values(id: impl Into<String>, physical_ms: u64, logical: u32) -> Self {
        Self {
            id: id.into(),
            physical_ms,
            logical,
        }
    }

    /// Increment the clock for a local event.
    /// Returns (physical_ms, logical) after advancement.
    pub fn tick(&mut self) -> (u64, u32) {
        let wall = current_time_ms();
        if wall > self.physical_ms {
            self.physical_ms = wall;
            self.logical = 0;
        } else {
            self.logical = self.logical.saturating_add(1);
        }
        (self.physical_ms, self.logical)
    }

    /// Merge with a remote clock.
    /// physical = max(local, remote, wall_clock).
    /// If physical advanced past both → logical = 0.
    /// If tied with one side → logical = max of tied sides + 1.
    pub fn merge(&mut self, remote: &HybridClock) -> (u64, u32) {
        let wall = current_time_ms();
        let new_physical = self.physical_ms.max(remote.physical_ms).max(wall);

        if new_physical > self.physical_ms && new_physical > remote.physical_ms {
            // Wall clock advanced past both — reset logical
            self.logical = 0;
        } else if new_physical == self.physical_ms && new_physical == remote.physical_ms {
            // All three tied — increment max logical
            self.logical = self.logical.max(remote.logical).saturating_add(1);
        } else if new_physical == self.physical_ms {
            // Local physical matches — increment our logical
            self.logical = self.logical.saturating_add(1);
        } else {
            // Remote physical matches — take remote logical + 1
            self.logical = remote.logical.saturating_add(1);
        }

        self.physical_ms = new_physical;
        (self.physical_ms, self.logical)
    }

    /// Compare two clocks for total ordering.
    /// Higher physical wins. Same physical → higher logical wins.
    /// Both equal → lower id wins (deterministic tiebreaker).
    pub fn cmp_order(&self, other: &HybridClock) -> std::cmp::Ordering {
        self.physical_ms
            .cmp(&other.physical_ms)
            .then_with(|| self.logical.cmp(&other.logical))
            .then_with(|| other.id.cmp(&self.id)) // lower id wins → reverse comparison
    }

    /// Compact representation for sorting: (physical_ms, logical).
    pub fn as_tuple(&self) -> (u64, u32) {
        (self.physical_ms, self.logical)
    }
}

// Keep the old name as a type alias for migration clarity in other modules.
// All code will use HybridClock directly — this alias exists only for documentation.
pub type LamportClock = HybridClock;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hlc_monotonic() {
        let mut clock = HybridClock::new("node-a");
        let (p1, l1) = clock.tick();
        let (p2, l2) = clock.tick();
        assert!(
            (p2, l2) >= (p1, l1),
            "clock must be monotonic: ({p1},{l1}) -> ({p2},{l2})"
        );
    }

    #[test]
    fn hlc_merge_advances() {
        let mut local = HybridClock::with_values("node-a", 100, 0);
        let remote = HybridClock::with_values("node-b", 200, 5);
        let (p, l) = local.merge(&remote);
        // Physical should be at least 200 (remote's physical)
        assert!(p >= 200, "merge should advance physical to at least remote");
        // If wall clock is > 200, logical resets. If == 200, logical = 5 + 1.
        // Either way, the clock advanced past the remote.
        assert!(
            (p, l) > (200, 5),
            "merged clock should be ahead of remote: ({p},{l}) vs (200,5)"
        );
    }

    #[test]
    fn hlc_merge_local_ahead() {
        let mut local = HybridClock::with_values("node-a", 500, 10);
        let remote = HybridClock::with_values("node-b", 100, 0);
        let (p, _l) = local.merge(&remote);
        assert!(p >= 500, "local was ahead — physical should stay >= 500");
    }

    #[test]
    fn hlc_tiebreak_deterministic() {
        let a = HybridClock::with_values("alpha", 100, 5);
        let b = HybridClock::with_values("beta", 100, 5);
        // Same physical + logical → lower id ("alpha") wins
        assert_eq!(a.cmp_order(&b), std::cmp::Ordering::Greater);
        assert_eq!(b.cmp_order(&a), std::cmp::Ordering::Less);
    }

    #[test]
    fn hlc_physical_beats_logical() {
        let a = HybridClock::with_values("node-a", 200, 0);
        let b = HybridClock::with_values("node-b", 100, 999);
        // a has higher physical — wins regardless of logical
        assert_eq!(a.cmp_order(&b), std::cmp::Ordering::Greater);
    }

    #[test]
    fn hlc_logical_breaks_physical_tie() {
        let a = HybridClock::with_values("node-a", 100, 10);
        let b = HybridClock::with_values("node-b", 100, 5);
        // Same physical → higher logical wins
        assert_eq!(a.cmp_order(&b), std::cmp::Ordering::Greater);
    }

    #[test]
    fn hlc_serialization_roundtrip() {
        let clock = HybridClock::with_values("node-x", 1711234567890, 42);
        let bytes = rmp_serde::to_vec(&clock).unwrap();
        let decoded: HybridClock = rmp_serde::from_slice(&bytes).unwrap();
        assert_eq!(clock, decoded);
    }

    #[test]
    fn hlc_logical_saturates() {
        let mut clock = HybridClock::with_values("node-a", u64::MAX, u32::MAX);
        let (p, l) = clock.tick();
        // If wall clock < u64::MAX (which it is), logical saturates
        assert_eq!(p, u64::MAX);
        assert_eq!(l, u32::MAX);
    }

    #[test]
    fn hlc_with_values_constructor() {
        let clock = HybridClock::with_values("test", 42, 7);
        assert_eq!(clock.id, "test");
        assert_eq!(clock.physical_ms, 42);
        assert_eq!(clock.logical, 7);
    }

    #[test]
    fn hlc_as_tuple() {
        let clock = HybridClock::with_values("test", 100, 5);
        assert_eq!(clock.as_tuple(), (100, 5));
    }
}
