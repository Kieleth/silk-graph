"""Test edge index consistency: all_edges() vs outgoing_edges() must agree.

Bug report from Shelob e2e: on production VMs, store.outgoing_edges(node_id)
returns [] for instance nodes, but all_edges() finds the same edges in a
full scan. The per-node index and the global edge list diverge.

These tests verify that all_edges() and outgoing_edges()/incoming_edges()
return consistent results across all lifecycle stages:
- After local add_edge
- After sync (merge_sync_payload)
- After redb persist + reopen
- After sync + persist + reopen (full production path)

If any test fails, the per-node edge index is stale or not rebuilt.
"""

import json
import os
import tempfile

import pytest

from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {
                "status": {"value_type": "string"},
            },
            "subtypes": {
                "instance": {
                    "properties": {
                        "hostname": {"value_type": "string", "required": True},
                        "host": {"value_type": "string"},
                        "port": {"value_type": "int"},
                        "priority": {"value_type": "int", "required": True},
                    },
                },
                "server": {
                    "properties": {
                        "name": {"value_type": "string", "required": True},
                        "provider": {"value_type": "string", "required": True},
                    },
                },
                "capability": {
                    "properties": {
                        "name": {"value_type": "string", "required": True},
                        "role": {"value_type": "string", "required": True},
                        "scope": {
                            "value_type": "string",
                            "required": True,
                            "constraints": {"enum": ["server", "fleet", "external"]},
                        },
                    },
                },
                "fleet": {
                    "properties": {
                        "name": {"value_type": "string"},
                    },
                },
            },
        },
    },
    "edge_types": {
        "RUNS_ON": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "MEMBER_OF": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "DEPENDS_ON": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "SCOPED_TO": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
    },
})


def make_store(instance_id: str, path: str | None = None) -> GraphStore:
    return GraphStore(instance_id, ONTOLOGY, path=path)


def _edges_from_all(store: GraphStore, source_id: str) -> list[dict]:
    """Filter all_edges() to find edges from a specific source."""
    return [e for e in store.all_edges() if e["source_id"] == source_id]


def _edges_to_all(store: GraphStore, target_id: str) -> list[dict]:
    """Filter all_edges() to find edges to a specific target."""
    return [e for e in store.all_edges() if e["target_id"] == target_id]


def _sync_a_to_b(store_a: GraphStore, store_b: GraphStore) -> int:
    """One-way sync: B gets A's data."""
    offer_b = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer_b)
    return store_b.merge_sync_payload(payload)


def _sync_bidirectional(store_a: GraphStore, store_b: GraphStore) -> tuple[int, int]:
    """Bidirectional sync. Returns (merged_on_b, merged_on_a)."""
    merged_b = _sync_a_to_b(store_a, store_b)
    merged_a = _sync_a_to_b(store_b, store_a)
    return merged_b, merged_a


def _add_shelob_topology(store: GraphStore, hostname: str = "gamma"):
    """Add the exact node/edge pattern Shelob uses: instance, server, fleet, capabilities, edges."""
    inst_id = f"inst-{hostname}"
    server_id = f"server-{hostname}"
    fleet_id = f"fleet-{hostname}"

    store.add_node(fleet_id, "entity", fleet_id, {"name": fleet_id}, subtype="fleet")
    store.add_node(server_id, "entity", server_id, {"name": hostname, "provider": "hetzner"}, subtype="server")
    store.add_node(inst_id, "entity", inst_id, {
        "hostname": hostname, "host": "10.0.0.1", "port": 8000, "priority": 100,
    }, subtype="instance")

    # Instance edges (the ones that go missing in production)
    store.add_edge(f"{inst_id}-RUNS_ON-{server_id}", "RUNS_ON", inst_id, server_id)
    store.add_edge(f"{inst_id}-MEMBER_OF-{fleet_id}", "MEMBER_OF", inst_id, fleet_id)

    # Capability with RUNS_ON (server-scoped)
    store.add_node(f"cap-runtime-{hostname}", "entity", "K3s", {
        "name": "K3s", "role": "container_runtime", "scope": "server",
    }, subtype="capability")
    store.add_edge(f"cap-runtime-{hostname}-RUNS_ON-{server_id}", "RUNS_ON", f"cap-runtime-{hostname}", server_id)

    # Capability with SCOPED_TO (fleet-scoped)
    store.add_node("cap-registry", "entity", "Registry", {
        "name": "Registry", "role": "container_registry", "scope": "fleet",
    }, subtype="capability")
    store.add_edge("cap-registry-SCOPED_TO-" + fleet_id, "SCOPED_TO", "cap-registry", fleet_id)
    store.add_edge(f"cap-registry-DEPENDS_ON-cap-runtime-{hostname}", "DEPENDS_ON", "cap-registry", f"cap-runtime-{hostname}")


def _assert_index_consistent(store: GraphStore, node_id: str, label: str = ""):
    """Assert that outgoing_edges(node_id) and all_edges() filtered by source_id agree."""
    from_index = store.outgoing_edges(node_id)
    from_scan = _edges_from_all(store, node_id)

    index_ids = {e["edge_id"] for e in from_index}
    scan_ids = {e["edge_id"] for e in from_scan}

    assert index_ids == scan_ids, (
        f"Edge index inconsistency{' (' + label + ')' if label else ''} for {node_id}: "
        f"outgoing_edges={index_ids}, all_edges scan={scan_ids}, "
        f"missing from index={scan_ids - index_ids}, "
        f"extra in index={index_ids - scan_ids}"
    )


def _assert_incoming_consistent(store: GraphStore, node_id: str, label: str = ""):
    """Assert that incoming_edges(node_id) and all_edges() filtered by target_id agree."""
    from_index = store.incoming_edges(node_id)
    from_scan = _edges_to_all(store, node_id)

    index_ids = {e["edge_id"] for e in from_index}
    scan_ids = {e["edge_id"] for e in from_scan}

    assert index_ids == scan_ids, (
        f"Incoming edge index inconsistency{' (' + label + ')' if label else ''} for {node_id}: "
        f"incoming_edges={index_ids}, all_edges scan={scan_ids}"
    )


# ── After local add_edge ────────────────────────────────────────────


class TestLocalEdgeConsistency:
    """outgoing_edges and all_edges agree after local add_edge."""

    def test_instance_runs_on(self):
        store = make_store("inst-a")
        _add_shelob_topology(store, "gamma")
        _assert_index_consistent(store, "inst-gamma", "local add")

    def test_instance_member_of(self):
        store = make_store("inst-a")
        _add_shelob_topology(store, "gamma")
        # MEMBER_OF should appear in outgoing
        edges = store.outgoing_edges("inst-gamma")
        types = {e["edge_type"] for e in edges}
        assert "MEMBER_OF" in types

    def test_capability_runs_on(self):
        store = make_store("inst-a")
        _add_shelob_topology(store, "gamma")
        _assert_index_consistent(store, "cap-runtime-gamma", "local cap")

    def test_server_incoming(self):
        store = make_store("inst-a")
        _add_shelob_topology(store, "gamma")
        _assert_incoming_consistent(store, "server-gamma", "local incoming")


# ── After sync ──────────────────────────────────────────────────────


class TestSyncEdgeConsistency:
    """outgoing_edges and all_edges agree on the RECEIVING side after sync."""

    def test_instance_edges_after_sync(self):
        """The production bug: instance RUNS_ON edge missing after sync."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        _add_shelob_topology(store_a, "gamma")
        _sync_a_to_b(store_a, store_b)

        # B should have gamma's edges findable via both APIs
        _assert_index_consistent(store_b, "inst-gamma", "after sync")

    def test_capability_edges_after_sync(self):
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        _add_shelob_topology(store_a, "gamma")
        _sync_a_to_b(store_a, store_b)

        _assert_index_consistent(store_b, "cap-runtime-gamma", "cap after sync")

    def test_fleet_scoped_edges_after_sync(self):
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        _add_shelob_topology(store_a, "gamma")
        _sync_a_to_b(store_a, store_b)

        _assert_index_consistent(store_b, "cap-registry", "fleet cap after sync")

    def test_bidirectional_sync_both_topologies(self):
        """Two instances with different seeds, bidirectional sync."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        _add_shelob_topology(store_a, "gamma")
        _add_shelob_topology(store_b, "delta")

        _sync_bidirectional(store_a, store_b)

        # A should see delta's edges
        _assert_index_consistent(store_a, "inst-delta", "bidir on A")
        # B should see gamma's edges
        _assert_index_consistent(store_b, "inst-gamma", "bidir on B")
        # Both should see their own edges too
        _assert_index_consistent(store_a, "inst-gamma", "bidir own on A")
        _assert_index_consistent(store_b, "inst-delta", "bidir own on B")


# ── After redb persist + reopen ─────────────────────────────────────


class TestPersistEdgeConsistency:
    """outgoing_edges and all_edges agree after redb close + reopen."""

    def test_instance_edges_after_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")

            store = make_store("inst-a", path=path)
            _add_shelob_topology(store, "gamma")
            _assert_index_consistent(store, "inst-gamma", "before reopen")
            del store

            store2 = make_store("inst-a", path=path)
            _assert_index_consistent(store2, "inst-gamma", "after reopen")

    def test_all_edges_count_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")

            store = make_store("inst-a", path=path)
            _add_shelob_topology(store, "gamma")
            count_before = len(store.all_edges())
            del store

            store2 = make_store("inst-a", path=path)
            count_after = len(store2.all_edges())
            assert count_before == count_after


# ── The full production path: sync + persist + reopen ───────────────


class TestSyncPersistReopenConsistency:
    """The exact production sequence that triggers the bug."""

    def test_sync_then_reopen(self):
        """A syncs to B, B persists, B reopens. Edges still findable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path_b = os.path.join(tmpdir, "b.redb")

            store_a = make_store("inst-a")
            store_b = make_store("inst-b", path=path_b)

            _add_shelob_topology(store_a, "gamma")
            _sync_a_to_b(store_a, store_b)

            _assert_index_consistent(store_b, "inst-gamma", "B after sync, before reopen")
            del store_b

            store_b2 = make_store("inst-b", path=path_b)
            _assert_index_consistent(store_b2, "inst-gamma", "B after sync + reopen")

    def test_bidirectional_sync_then_both_reopen(self):
        """Both instances sync, both persist, both reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path_a = os.path.join(tmpdir, "a.redb")
            path_b = os.path.join(tmpdir, "b.redb")

            store_a = make_store("inst-a", path=path_a)
            store_b = make_store("inst-b", path=path_b)

            _add_shelob_topology(store_a, "gamma")
            _add_shelob_topology(store_b, "delta")

            _sync_bidirectional(store_a, store_b)

            # Verify before reopen
            _assert_index_consistent(store_a, "inst-delta", "A pre-reopen")
            _assert_index_consistent(store_b, "inst-gamma", "B pre-reopen")

            del store_a, store_b

            store_a2 = make_store("inst-a", path=path_a)
            store_b2 = make_store("inst-b", path=path_b)

            _assert_index_consistent(store_a2, "inst-delta", "A after reopen")
            _assert_index_consistent(store_b2, "inst-gamma", "B after reopen")
            _assert_index_consistent(store_a2, "inst-gamma", "A own after reopen")
            _assert_index_consistent(store_b2, "inst-delta", "B own after reopen")

    def test_multiple_sync_rounds_then_reopen(self):
        """Multiple sync rounds (like production tick loop), then reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path_a = os.path.join(tmpdir, "a.redb")
            path_b = os.path.join(tmpdir, "b.redb")

            store_a = make_store("inst-a", path=path_a)
            store_b = make_store("inst-b", path=path_b)

            _add_shelob_topology(store_a, "gamma")

            # 5 sync rounds (production does every 5s)
            for _ in range(5):
                _sync_bidirectional(store_a, store_b)

            _assert_index_consistent(store_b, "inst-gamma", "B after 5 rounds")

            del store_a, store_b

            store_b2 = make_store("inst-b", path=path_b)
            _assert_index_consistent(store_b2, "inst-gamma", "B after 5 rounds + reopen")

    def test_add_edges_on_b_then_sync_then_reopen(self):
        """B has its own topology, syncs with A, reopens. Both topologies intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path_b = os.path.join(tmpdir, "b.redb")

            store_a = make_store("inst-a")
            store_b = make_store("inst-b", path=path_b)

            # B has its own seed (like delta in production)
            _add_shelob_topology(store_b, "delta")
            # A has its seed (like gamma)
            _add_shelob_topology(store_a, "gamma")

            # Bidirectional sync
            _sync_bidirectional(store_a, store_b)

            # B should see both gamma and delta edges
            _assert_index_consistent(store_b, "inst-gamma", "B sees gamma")
            _assert_index_consistent(store_b, "inst-delta", "B sees own delta")

            del store_b

            store_b2 = make_store("inst-b", path=path_b)
            _assert_index_consistent(store_b2, "inst-gamma", "B sees gamma after reopen")
            _assert_index_consistent(store_b2, "inst-delta", "B sees delta after reopen")
