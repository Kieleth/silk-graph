use serde::{Deserialize, Serialize};

/// Lamport logical clock for causal ordering across distributed nodes.
///
/// Rules:
/// - Incremented before each local event
/// - On receive: local = max(local, remote) + 1
/// - Ties broken deterministically by instance_id (lexicographic)
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LamportClock {
    /// Unique identifier of the instance that owns this clock
    pub id: String,
    /// Monotonically increasing logical time
    pub time: u64,
}

impl LamportClock {
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            time: 0,
        }
    }

    /// Increment the clock for a local event. Returns the new time.
    /// S-01: uses saturating_add to prevent overflow at u64::MAX.
    pub fn tick(&mut self) -> u64 {
        self.time = self.time.saturating_add(1);
        self.time
    }

    /// Merge with a remote clock: local = max(local, remote) + 1.
    /// S-01: uses saturating_add to prevent overflow at u64::MAX.
    pub fn merge(&mut self, remote_time: u64) -> u64 {
        self.time = self.time.max(remote_time).saturating_add(1);
        self.time
    }

    /// Compare two clocks for total ordering.
    /// Higher time wins. Equal time: lexicographically lower id wins.
    /// Returns Ordering from self's perspective.
    pub fn cmp_order(&self, other: &LamportClock) -> std::cmp::Ordering {
        self.time
            .cmp(&other.time)
            .then_with(|| other.id.cmp(&self.id)) // lower id wins → reverse comparison
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lamport_monotonic() {
        let mut clock = LamportClock::new("node-a");
        let t1 = clock.tick();
        let t2 = clock.tick();
        let t3 = clock.tick();
        assert!(t1 < t2);
        assert!(t2 < t3);
        assert_eq!(t3, 3);
    }

    #[test]
    fn lamport_merge_takes_max() {
        let mut clock = LamportClock::new("node-a");
        clock.time = 5;
        let new_time = clock.merge(8);
        assert_eq!(new_time, 9);
    }

    #[test]
    fn lamport_merge_local_ahead() {
        let mut clock = LamportClock::new("node-a");
        clock.time = 10;
        let new_time = clock.merge(3);
        assert_eq!(new_time, 11);
    }

    #[test]
    fn lamport_tiebreak_deterministic() {
        let a = LamportClock {
            id: "alpha".into(),
            time: 5,
        };
        let b = LamportClock {
            id: "beta".into(),
            time: 5,
        };
        // Same time → lower id ("alpha") wins
        assert_eq!(a.cmp_order(&b), std::cmp::Ordering::Greater); // a wins
        assert_eq!(b.cmp_order(&a), std::cmp::Ordering::Less); // b loses
    }

    #[test]
    fn lamport_higher_time_wins() {
        let a = LamportClock {
            id: "alpha".into(),
            time: 10,
        };
        let b = LamportClock {
            id: "beta".into(),
            time: 5,
        };
        assert_eq!(a.cmp_order(&b), std::cmp::Ordering::Greater);
    }

    #[test]
    fn lamport_serialization_roundtrip() {
        let clock = LamportClock {
            id: "node-x".into(),
            time: 42,
        };
        let bytes = rmp_serde::to_vec(&clock).unwrap();
        let decoded: LamportClock = rmp_serde::from_slice(&bytes).unwrap();
        assert_eq!(clock, decoded);
    }

    // S-01: clock overflow protection
    #[test]
    fn tick_saturates_at_max() {
        let mut clock = LamportClock {
            id: "node-a".into(),
            time: u64::MAX,
        };
        let t = clock.tick();
        assert_eq!(t, u64::MAX); // saturates, doesn't wrap to 0
    }

    #[test]
    fn merge_saturates_at_max() {
        let mut clock = LamportClock {
            id: "node-a".into(),
            time: 0,
        };
        let t = clock.merge(u64::MAX);
        assert_eq!(t, u64::MAX); // max + 1 saturates to max
    }

    #[test]
    fn tick_near_max_saturates() {
        let mut clock = LamportClock {
            id: "node-a".into(),
            time: u64::MAX - 1,
        };
        assert_eq!(clock.tick(), u64::MAX);
        assert_eq!(clock.tick(), u64::MAX); // stays at max
    }
}
