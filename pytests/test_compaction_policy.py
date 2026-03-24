"""Compaction policies — automatic oplog management.

Tests for IntervalPolicy, ThresholdPolicy, and the CompactionPolicy protocol.
"""

import time
from silk import GraphStore, CompactionPolicy, IntervalPolicy, ThresholdPolicy

ONTOLOGY = {
    "node_types": {"entity": {"properties": {}}},
    "edge_types": {}
}


def _store():
    return GraphStore("test", ONTOLOGY)


# -- ThresholdPolicy --


def test_threshold_no_compact_below():
    """Below threshold: no compaction."""
    store = _store()
    store.add_node("n1", "entity", "A")
    policy = ThresholdPolicy(max_entries=100)
    result = policy.check(store)
    assert result is None
    assert store.len() > 1  # not compacted


def test_threshold_compacts_above():
    """Above threshold: compaction triggered."""
    store = _store()
    for i in range(20):
        store.add_node(f"n{i}", "entity", f"Node {i}")
    assert store.len() > 20  # genesis + 20 nodes

    policy = ThresholdPolicy(max_entries=10)
    result = policy.check(store)
    assert result is not None  # compaction happened
    assert len(result) == 64  # hex hash
    assert store.len() == 1  # compacted to checkpoint


def test_threshold_preserves_data():
    """Compaction via policy preserves all nodes."""
    store = _store()
    for i in range(15):
        store.add_node(f"n{i}", "entity", f"Node {i}")

    policy = ThresholdPolicy(max_entries=5)
    policy.check(store)

    for i in range(15):
        assert store.get_node(f"n{i}") is not None


def test_threshold_should_compact():
    """should_compact returns bool correctly."""
    store = _store()
    for i in range(10):
        store.add_node(f"n{i}", "entity", f"Node {i}")

    policy = ThresholdPolicy(max_entries=100)
    assert policy.should_compact(store) is False

    policy2 = ThresholdPolicy(max_entries=5)
    assert policy2.should_compact(store) is True


# -- IntervalPolicy --


def test_interval_first_check_compacts():
    """First check always compacts (last_compact is 0)."""
    store = _store()
    store.add_node("n1", "entity", "A")

    policy = IntervalPolicy(seconds=0.01)
    time.sleep(0.02)
    result = policy.check(store)
    assert result is not None
    assert store.len() == 1


def test_interval_second_check_skips():
    """Second check within interval: no compaction."""
    store = _store()
    store.add_node("n1", "entity", "A")

    policy = IntervalPolicy(seconds=10.0)  # 10 second interval
    policy.check(store)  # first: compacts

    store.add_node("n2", "entity", "B")
    result = policy.check(store)  # second: too soon
    assert result is None
    assert store.len() == 2  # checkpoint + new node, not re-compacted


def test_interval_compacts_after_wait():
    """After interval passes: compaction happens again."""
    store = _store()
    store.add_node("n1", "entity", "A")

    policy = IntervalPolicy(seconds=0.01)
    policy.check(store)

    store.add_node("n2", "entity", "B")
    time.sleep(0.02)
    result = policy.check(store)
    assert result is not None
    assert store.len() == 1  # re-compacted


# -- CompactionPolicy protocol --


def test_custom_policy():
    """Custom policy via the protocol."""
    class AlwaysCompact:
        def should_compact(self, store):
            return True

        def check(self, store):
            if self.should_compact(store):
                return store.compact()
            return None

    store = _store()
    store.add_node("n1", "entity", "A")

    policy = AlwaysCompact()
    result = policy.check(store)
    assert result is not None
    assert store.len() == 1


def test_custom_policy_never():
    """Custom policy that never compacts."""
    class NeverCompact:
        def should_compact(self, store):
            return False

        def check(self, store):
            return None

    store = _store()
    store.add_node("n1", "entity", "A")

    policy = NeverCompact()
    result = policy.check(store)
    assert result is None
    assert store.len() > 1


def test_protocol_check():
    """CompactionPolicy is runtime-checkable."""
    class Good:
        def should_compact(self, store): return False
        def check(self, store): return None

    class Bad:
        pass

    assert isinstance(Good(), CompactionPolicy)
    assert not isinstance(Bad(), CompactionPolicy)


def test_builtin_policies_implement_protocol():
    """Built-in policies satisfy the protocol."""
    assert isinstance(IntervalPolicy(seconds=60), CompactionPolicy)
    assert isinstance(ThresholdPolicy(max_entries=100), CompactionPolicy)
