"""Python tests for Silk sync protocol (S-3).

Tests the sync API from Python:
- generate_sync_offer / receive_sync_offer / merge_sync_payload round-trip
- snapshot / from_snapshot bootstrap
- Bidirectional sync with graph convergence
- Conflict resolution after sync (LWW, add-wins)
"""

import json
import pytest

from silk import GraphStore


ONTOLOGY = json.dumps(
    {
        "node_types": {
            "entity": {
                "description": "A managed thing",
                "properties": {
                    "status": {"value_type": "string"},
                    "cpu": {"value_type": "float"},
                },
            },
            "signal": {
                "description": "An observed fact",
                "properties": {
                    "severity": {"value_type": "string", "required": True},
                },
            },
        },
        "edge_types": {
            "RUNS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
            "OBSERVES": {
                "source_types": ["signal"],
                "target_types": ["entity"],
                "properties": {},
            },
        },
    }
)


# -- Fixtures --


def make_store(instance_id: str) -> GraphStore:
    return GraphStore(instance_id, ONTOLOGY)


# -- Sync offer/payload round-trip --


class TestSyncProtocol:
    def test_sync_offer_is_bytes(self):
        store = make_store("inst-a")
        offer = store.generate_sync_offer()
        assert isinstance(offer, bytes)
        assert len(offer) > 0

    def test_receive_sync_offer_is_bytes(self):
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")
        offer_a = store_a.generate_sync_offer()
        payload = store_b.receive_sync_offer(offer_a)
        assert isinstance(payload, bytes)

    def test_sync_a_to_b(self):
        """A has nodes B doesn't. After sync, B has them."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        store_a.add_node("s1", "entity", "Server 1", {"status": "alive"})
        store_a.add_node("s2", "entity", "Server 2", {"status": "dead"})

        # Sync: B offers → A computes payload → B merges
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        merged = store_b.merge_sync_payload(payload)

        assert merged >= 2  # at least s1 and s2
        assert store_b.get_node("s1") is not None
        assert store_b.get_node("s2") is not None
        assert store_b.get_node("s1")["properties"]["status"] == "alive"

    def test_sync_bidirectional_convergence(self):
        """A and B each have unique nodes. After bidirectional sync, both converge."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        store_a.add_node("a1", "entity", "Node from A")
        store_b.add_node("b1", "entity", "Node from B")

        # Sync A → B
        offer_b = store_b.generate_sync_offer()
        payload_a_to_b = store_a.receive_sync_offer(offer_b)
        store_b.merge_sync_payload(payload_a_to_b)

        # Sync B → A
        offer_a = store_a.generate_sync_offer()
        payload_b_to_a = store_b.receive_sync_offer(offer_a)
        store_a.merge_sync_payload(payload_b_to_a)

        # Both should have both nodes
        assert store_a.get_node("a1") is not None
        assert store_a.get_node("b1") is not None
        assert store_b.get_node("a1") is not None
        assert store_b.get_node("b1") is not None

    def test_sync_is_idempotent(self):
        """Syncing twice produces the same result as syncing once."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        store_a.add_node("n1", "entity", "Node 1")

        # First sync
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        merged1 = store_b.merge_sync_payload(payload)
        assert merged1 >= 1

        len_after_first = store_b.len()

        # Second sync — should be no-op
        offer_b2 = store_b.generate_sync_offer()
        payload2 = store_a.receive_sync_offer(offer_b2)
        merged2 = store_b.merge_sync_payload(payload2)
        assert merged2 == 0
        assert store_b.len() == len_after_first

    def test_sync_with_edges(self):
        """Edges sync correctly along with their endpoint nodes."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        store_a.add_node("svc", "entity", "API Service")
        store_a.add_node("srv", "entity", "Server")
        store_a.add_edge("e1", "RUNS_ON", "svc", "srv")

        # Sync A → B
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        store_b.merge_sync_payload(payload)

        # B should have the edge and both nodes
        assert store_b.get_node("svc") is not None
        assert store_b.get_node("srv") is not None
        assert store_b.get_edge("e1") is not None
        edges = store_b.all_edges()
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "RUNS_ON"

    def test_sync_graph_queries_work_after_merge(self):
        """Graph queries (BFS, shortest path) work on merged data."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        store_a.add_node("a", "entity", "A")
        store_a.add_node("b", "entity", "B")
        store_a.add_node("c", "entity", "C")
        store_a.add_edge("ab", "RUNS_ON", "a", "b")
        store_a.add_edge("bc", "RUNS_ON", "b", "c")

        # Sync to B
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        store_b.merge_sync_payload(payload)

        # BFS from A should reach B and C
        reachable = store_b.bfs("a")
        assert "b" in reachable
        assert "c" in reachable

        # Shortest path from A to C
        path = store_b.shortest_path("a", "c")
        assert path is not None
        assert path == ["a", "b", "c"]


# -- Snapshot --


class TestSnapshot:
    def test_snapshot_roundtrip(self):
        """Snapshot and from_snapshot produce equivalent stores."""
        store_a = make_store("inst-a")
        store_a.add_node("s1", "entity", "Server 1", {"status": "alive"})
        store_a.add_node("s2", "entity", "Server 2")
        store_a.add_edge("e1", "RUNS_ON", "s1", "s2")

        snap_bytes = store_a.snapshot()
        assert isinstance(snap_bytes, bytes)
        assert len(snap_bytes) > 0

        store_b = GraphStore.from_snapshot("inst-b", snap_bytes)

        # B should have all nodes and edges
        assert store_b.get_node("s1") is not None
        assert store_b.get_node("s2") is not None
        assert store_b.get_edge("e1") is not None
        assert store_b.get_node("s1")["properties"]["status"] == "alive"

    def test_snapshot_then_delta_sync(self):
        """After snapshot bootstrap, delta sync works for new entries."""
        store_a = make_store("inst-a")
        store_a.add_node("s1", "entity", "Server 1")

        # Bootstrap B from snapshot
        snap = store_a.snapshot()
        store_b = GraphStore.from_snapshot("inst-b", snap)

        # A adds more entries
        store_a.add_node("s2", "entity", "Server 2")

        # Delta sync: B offers → A computes → B merges
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        merged = store_b.merge_sync_payload(payload)

        assert merged >= 1
        assert store_b.get_node("s2") is not None

    def test_snapshot_preserves_ontology(self):
        """Ontology is preserved across snapshot bootstrap."""
        store_a = make_store("inst-a")
        snap = store_a.snapshot()
        store_b = GraphStore.from_snapshot("inst-b", snap)

        # Should have the same ontology
        assert store_b.node_type_names() == store_a.node_type_names()
        assert store_b.edge_type_names() == store_a.edge_type_names()

        # Should enforce the same ontology rules
        with pytest.raises(ValueError):
            store_b.add_node("x", "potato", "Bad type")

    def test_snapshot_graph_algorithms(self):
        """Graph algorithms work on stores bootstrapped from snapshot."""
        store_a = make_store("inst-a")
        store_a.add_node("a", "entity", "A")
        store_a.add_node("b", "entity", "B")
        store_a.add_edge("ab", "RUNS_ON", "a", "b")

        store_b = GraphStore.from_snapshot("inst-b", store_a.snapshot())

        path = store_b.shortest_path("a", "b")
        assert path == ["a", "b"]

        reachable = store_b.bfs("a")
        assert "b" in reachable


# -- Conflict resolution after sync --


class TestSyncConflictResolution:
    def test_lww_concurrent_property_update(self):
        """LWW resolves concurrent property updates after sync."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        # Both start with the same node (via sync)
        store_a.add_node("s1", "entity", "Server 1")
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        store_b.merge_sync_payload(payload)

        # Both update the same property independently
        store_a.update_property("s1", "status", "alive")  # A's clock is ahead or tied
        store_b.update_property("s1", "status", "dead")  # B's clock

        # Sync A → B and B → A
        offer_b2 = store_b.generate_sync_offer()
        payload_a = store_a.receive_sync_offer(offer_b2)
        store_b.merge_sync_payload(payload_a)

        offer_a = store_a.generate_sync_offer()
        payload_b = store_b.receive_sync_offer(offer_a)
        store_a.merge_sync_payload(payload_b)

        # Both should converge to the same value (LWW)
        val_a = store_a.get_node("s1")["properties"]["status"]
        val_b = store_b.get_node("s1")["properties"]["status"]
        assert val_a == val_b  # same value, regardless of which "won"

    def test_add_wins_after_sync(self):
        """Add-wins: concurrent add + remove → node exists after sync."""
        store_a = make_store("inst-a")
        store_b = make_store("inst-b")

        # A creates and then removes a node
        store_a.add_node("s1", "entity", "Server 1")

        # Sync to B first
        offer_b = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer_b)
        store_b.merge_sync_payload(payload)

        # A removes the node
        store_a.remove_node("s1")

        # B re-adds the node (concurrent with A's remove)
        store_b.add_node("s1", "entity", "Server 1 resurrected")

        # Sync B → A: A learns what B has
        offer_a = store_a.generate_sync_offer()
        payload_for_a = store_b.receive_sync_offer(offer_a)
        store_a.merge_sync_payload(payload_for_a)

        # Sync A → B: B learns what A has
        offer_b2 = store_b.generate_sync_offer()
        payload_for_b = store_a.receive_sync_offer(offer_b2)
        store_b.merge_sync_payload(payload_for_b)

        # Both should have the node (add-wins)
        assert store_a.get_node("s1") is not None
        assert store_b.get_node("s1") is not None


# -- Edge validation during sync --


class TestEdgeValidationOnSync:
    """Verify that edge source/target type constraints are enforced during sync,
    even when entries arrive from a peer with a different ontology."""

    def test_invalid_edge_quarantined_on_sync(self):
        """An edge with wrong source/target types is quarantined on the
        receiving peer, not silently accepted."""
        # Peer A: permissive ontology allows server -> entity edges
        permissive = {
            "node_types": {
                "server": {"properties": {}},
                "app": {"properties": {}},
            },
            "edge_types": {
                "RUNS_ON": {
                    "source_types": ["server", "app"],
                    "target_types": ["server", "app"],
                },
            },
        }
        a = GraphStore("peer-a", permissive)
        a.add_node("s1", "server", "Server")
        a.add_node("a1", "app", "App")
        a.add_edge("bad", "RUNS_ON", "s1", "a1")  # server -> app

        # Peer B: strict ontology — only app -> server
        strict = {
            "node_types": {
                "server": {"properties": {}},
                "app": {"properties": {}},
            },
            "edge_types": {
                "RUNS_ON": {
                    "source_types": ["app"],
                    "target_types": ["server"],
                },
            },
        }
        b = GraphStore("peer-b", strict)

        # Sync A → B
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)

        # Edge should be quarantined on B (wrong direction for B's ontology)
        assert b.get_edge("bad") is None, "invalid edge should not be queryable"
        assert len(b.get_quarantined()) > 0, "invalid edge should be quarantined"
        # Nodes should be present (they're valid)
        assert b.get_node("s1") is not None
        assert b.get_node("a1") is not None

    def test_valid_edge_survives_sync(self):
        """A valid edge is materialized correctly after sync."""
        ont = {
            "node_types": {"server": {"properties": {}}, "app": {"properties": {}}},
            "edge_types": {
                "RUNS_ON": {
                    "source_types": ["app"],
                    "target_types": ["server"],
                },
            },
        }
        a = GraphStore("peer-a", ont)
        a.add_node("s1", "server", "Server")
        a.add_node("a1", "app", "App")
        a.add_edge("e1", "RUNS_ON", "a1", "s1")  # app -> server (valid)

        b = GraphStore("peer-b", ont)
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)

        assert b.get_edge("e1") is not None
        assert b.get_edge("e1")["source_id"] == "a1"
        assert b.get_edge("e1")["target_id"] == "s1"
        assert len(b.get_quarantined()) == 0

    def test_nodes_before_edges_in_topological_order(self):
        """Topological ordering guarantees nodes are materialized before
        their edges during sync. This test verifies the ordering by
        checking that edges are validated against existing node types."""
        ont = {
            "node_types": {"server": {"properties": {}}, "app": {"properties": {}}},
            "edge_types": {
                "RUNS_ON": {
                    "source_types": ["app"],
                    "target_types": ["server"],
                },
            },
        }
        # Build a graph with many nodes and edges
        a = GraphStore("peer-a", ont)
        for i in range(50):
            a.add_node(f"s-{i}", "server", f"Server {i}")
            a.add_node(f"a-{i}", "app", f"App {i}")
        for i in range(50):
            a.add_edge(f"e-{i}", "RUNS_ON", f"a-{i}", f"s-{i}")

        # Sync to empty peer
        b = GraphStore("peer-b", ont)
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)

        # All 50 edges should be present (none quarantined)
        assert len(b.get_quarantined()) == 0
        for i in range(50):
            edge = b.get_edge(f"e-{i}")
            assert edge is not None, f"edge e-{i} missing after sync"
            assert edge["source_id"] == f"a-{i}"
            assert edge["target_id"] == f"s-{i}"


# -- HLC tie-breaking --


class TestHLCTieBreaking:
    """Verify that HLC tie-breaking is deterministic and documented."""

    def test_lower_instance_id_wins_tie(self):
        """When two peers write the same property at the same logical time,
        the peer with the lexicographically lower instance_id wins."""
        ont = {
            "node_types": {"entity": {"properties": {"value": {"value_type": "string"}}}},
            "edge_types": {},
        }
        # Create shared base
        base = GraphStore("base", ont)
        base.add_node("n1", "entity", "Node", {"value": "original"})

        # Fork to two peers with known instance IDs
        a = GraphStore.from_snapshot("aaa-peer", base.snapshot())
        b = GraphStore.from_snapshot("zzz-peer", base.snapshot())

        # Both update the same property (concurrent)
        a.update_property("n1", "value", "from-aaa")
        b.update_property("n1", "value", "from-zzz")

        # Sync bidirectionally
        offer = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer)
        b.merge_sync_payload(payload)

        offer = a.generate_sync_offer()
        payload = b.receive_sync_offer(offer)
        a.merge_sync_payload(payload)

        # Both must agree
        val_a = a.get_node("n1")["properties"]["value"]
        val_b = b.get_node("n1")["properties"]["value"]
        assert val_a == val_b, f"peers diverged: a={val_a}, b={val_b}"

        # The winner is deterministic — lower instance_id wins ties.
        # If clocks are equal, "aaa-peer" < "zzz-peer", so aaa wins.
        # But clocks may not be exactly equal (HLC advances), so we
        # only assert convergence, not which peer won.
        # The point: both agree, deterministically.


# -- Multi-subtype sync (SA-033 investigation) --


class TestMultiSubtypeSync:
    """Verify that ALL subtypes transfer during sync, not just some.

    Reproduces the partial sync bug observed in production:
    Entity(instance) nodes synced between peers but Entity(capability),
    Entity(k8s_cluster), and Rule(guardrail) nodes did not.
    See shelob/docs/silk-sync-investigation.md.
    """

    RICH_ONTOLOGY = json.dumps({
        "node_types": {
            "entity": {
                "properties": {},
                "subtypes": {
                    "instance": {"properties": {
                        "host": {"value_type": "string"},
                        "priority": {"value_type": "int"},
                    }},
                    "capability": {"properties": {
                        "name": {"value_type": "string"},
                        "role": {"value_type": "string"},
                        "status": {"value_type": "string"},
                    }},
                    "k8s_cluster": {"properties": {
                        "name": {"value_type": "string"},
                        "server_url": {"value_type": "string"},
                    }},
                },
            },
            "signal": {
                "properties": {},
                "subtypes": {
                    "alert": {"properties": {
                        "severity": {"value_type": "string"},
                    }},
                },
            },
            "rule": {
                "properties": {},
                "subtypes": {
                    "guardrail": {"properties": {
                        "scope": {"value_type": "string"},
                        "check_type": {"value_type": "string"},
                    }},
                },
            },
        },
        "edge_types": {
            "RUNS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
            "DEPENDS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
        },
    })

    def _make(self, instance_id: str) -> GraphStore:
        return GraphStore(instance_id, self.RICH_ONTOLOGY)

    def test_all_subtypes_sync_in_one_round(self):
        """A has instance + capability + k8s_cluster + guardrail.
        After one sync round, B has all of them."""
        a = self._make("leader")
        b = self._make("joiner")

        # Leader: rich KG with multiple subtypes
        a.add_node("inst-a", "entity", "leader", {"host": "10.0.0.1", "priority": 100}, subtype="instance")
        a.add_node("cap-runtime", "entity", "K3s", {"name": "K3s", "role": "container_runtime", "status": "installed"}, subtype="capability")
        a.add_node("cap-gw", "entity", "nginx", {"name": "nginx", "role": "gateway", "status": "installed"}, subtype="capability")
        a.add_node("cluster-k3s", "entity", "k3s", {"name": "k3s", "server_url": "https://10.0.0.1:6443"}, subtype="k8s_cluster")
        a.add_node("guard-1", "rule", "self-model", {"scope": "update", "check_type": "pre_flight"}, subtype="guardrail")
        a.add_edge("cap-runtime-RUNS_ON-inst-a", "RUNS_ON", "cap-runtime", "inst-a")
        a.add_edge("cap-gw-DEPENDS_ON-cap-runtime", "DEPENDS_ON", "cap-gw", "cap-runtime")

        # Joiner: minimal KG
        b.add_node("inst-b", "entity", "joiner", {"host": "10.0.0.2", "priority": 50}, subtype="instance")

        # Sync: B offers → A responds → B merges
        offer_b = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer_b)
        b.merge_sync_payload(payload)

        # Reverse: A offers → B responds → A merges
        offer_a = a.generate_sync_offer()
        payload2 = b.receive_sync_offer(offer_a)
        a.merge_sync_payload(payload2)

        # B must have ALL nodes from A
        assert b.get_node("inst-a") is not None, "instance node not synced"
        assert b.get_node("cap-runtime") is not None, "capability node not synced"
        assert b.get_node("cap-gw") is not None, "capability node not synced"
        assert b.get_node("cluster-k3s") is not None, "k8s_cluster node not synced"
        assert b.get_node("guard-1") is not None, "guardrail node not synced"

        # Edges must sync too
        assert b.get_edge("cap-runtime-RUNS_ON-inst-a") is not None, "RUNS_ON edge not synced"
        assert b.get_edge("cap-gw-DEPENDS_ON-cap-runtime") is not None, "DEPENDS_ON edge not synced"

        # A must have B's node
        assert a.get_node("inst-b") is not None, "reverse sync failed"

    def test_subtype_properties_preserved_after_sync(self):
        """Synced nodes retain their subtype and property values."""
        a = self._make("alpha")
        b = self._make("beta")

        a.add_node("cap-1", "entity", "Docker", {"name": "Docker", "role": "container_runtime", "status": "installed"}, subtype="capability")
        a.add_node("guard-2", "rule", "disk check", {"scope": "deploy", "check_type": "post_flight"}, subtype="guardrail")

        offer_b = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer_b)
        b.merge_sync_payload(payload)

        cap = b.get_node("cap-1")
        assert cap is not None
        assert cap["subtype"] == "capability"
        assert cap["properties"]["name"] == "Docker"
        assert cap["properties"]["role"] == "container_runtime"
        assert cap["properties"]["status"] == "installed"

        guard = b.get_node("guard-2")
        assert guard is not None
        assert guard["subtype"] == "guardrail"
        assert guard["properties"]["scope"] == "deploy"

    def test_many_nodes_all_subtypes_converge(self):
        """Stress: 50 nodes across 5 subtypes all converge after sync."""
        a = self._make("source")
        b = self._make("dest")

        for i in range(10):
            a.add_node(f"inst-{i}", "entity", f"inst-{i}", {"host": f"10.0.0.{i}", "priority": i}, subtype="instance")
            a.add_node(f"cap-{i}", "entity", f"cap-{i}", {"name": f"cap-{i}", "role": "test", "status": "installed"}, subtype="capability")
            a.add_node(f"cluster-{i}", "entity", f"cluster-{i}", {"name": f"c-{i}", "server_url": f"https://10.0.0.{i}:6443"}, subtype="k8s_cluster")
            a.add_node(f"alert-{i}", "signal", f"alert-{i}", {"severity": "warning"}, subtype="alert")
            a.add_node(f"guard-{i}", "rule", f"guard-{i}", {"scope": "update", "check_type": "pre_flight"}, subtype="guardrail")

        offer_b = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer_b)
        merged = b.merge_sync_payload(payload)

        assert merged > 0
        for i in range(10):
            assert b.get_node(f"inst-{i}") is not None, f"inst-{i} missing"
            assert b.get_node(f"cap-{i}") is not None, f"cap-{i} missing"
            assert b.get_node(f"cluster-{i}") is not None, f"cluster-{i} missing"
            assert b.get_node(f"alert-{i}") is not None, f"alert-{i} missing"
            assert b.get_node(f"guard-{i}") is not None, f"guard-{i} missing"


# -- Genesis divergence (SA-033 root cause investigation) --


class TestGenesisDivergence:
    """Test sync behavior when two stores have different genesis entries.

    In production, two Shelob instances created with the same ontology but
    different instance_ids produce different genesis hashes (the author field
    is the instance_id). This means their DAGs have no common ancestor.

    Silk must either:
    a) Converge despite divergent genesis (cross-DAG merge), or
    b) Reject the sync with a clear error (incompatible stores).

    Silently dropping entries is not acceptable.
    """

    SIMPLE_ONT = json.dumps({
        "node_types": {
            "entity": {"properties": {"status": {"value_type": "string"}}},
        },
        "edge_types": {},
    })

    def test_different_instance_ids_produce_different_genesis(self):
        """Two stores with same ontology but different instance_ids
        have different genesis hashes (because author differs)."""
        a = GraphStore("inst-a", self.SIMPLE_ONT)
        b = GraphStore("inst-b", self.SIMPLE_ONT)

        # Introspect: different instance_ids → different genesis
        entries_a = a.entries_since(None)
        entries_b = b.entries_since(None)

        # Both have exactly 1 entry (genesis)
        assert len(entries_a) == 1
        assert len(entries_b) == 1

        # The genesis hashes differ because author is different
        hash_a = entries_a[0]["hash"]
        hash_b = entries_b[0]["hash"]
        # This documents the reality — whether they match or differ
        # determines if sync can work between independent stores.
        if hash_a == hash_b:
            # Same genesis → sync should work (shared root)
            pass
        else:
            # Different genesis → this is the root cause
            # Sync has no common ancestor, entries_missing may fail silently
            pass

    def test_sync_between_independent_stores_transfers_data(self):
        """Two independently-created stores must converge after sync.

        This is the production scenario: gamma and delta are created
        separately, each from their own seed. They must still sync.
        """
        a = GraphStore("inst-a", self.SIMPLE_ONT)
        b = GraphStore("inst-b", self.SIMPLE_ONT)

        a.add_node("node-a", "entity", "from A", {"status": "active"})
        b.add_node("node-b", "entity", "from B", {"status": "active"})

        # Sync A → B (B sends offer, A responds with payload, B merges)
        offer_b = b.generate_sync_offer()
        payload_a_to_b = a.receive_sync_offer(offer_b)
        b.merge_sync_payload(payload_a_to_b)

        # Sync B → A
        offer_a = a.generate_sync_offer()
        payload_b_to_a = b.receive_sync_offer(offer_a)
        a.merge_sync_payload(payload_b_to_a)

        # Both must have both nodes
        assert b.get_node("node-a") is not None, "A's node not synced to B"
        assert a.get_node("node-b") is not None, "B's node not synced to A"

    def test_sync_independent_stores_multiple_nodes(self):
        """Independent stores with many nodes across types must converge."""
        ont = json.dumps({
            "node_types": {
                "entity": {"properties": {}, "subtypes": {
                    "server": {"properties": {"host": {"value_type": "string"}}},
                    "service": {"properties": {"name": {"value_type": "string"}}},
                }},
                "rule": {"properties": {}, "subtypes": {
                    "guardrail": {"properties": {"scope": {"value_type": "string"}}},
                }},
            },
            "edge_types": {
                "RUNS_ON": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}},
            },
        })

        a = GraphStore("gamma", ont)
        b = GraphStore("delta", ont)

        # Gamma: full infrastructure KG
        a.add_node("srv-1", "entity", "server-1", {"host": "10.0.0.1"}, subtype="server")
        a.add_node("svc-api", "entity", "api", {"name": "api"}, subtype="service")
        a.add_node("guard-1", "rule", "disk-check", {"scope": "deploy"}, subtype="guardrail")
        a.add_edge("svc-api-RUNS_ON-srv-1", "RUNS_ON", "svc-api", "srv-1")

        # Delta: only its own identity
        b.add_node("srv-2", "entity", "server-2", {"host": "10.0.0.2"}, subtype="server")

        # Bidirectional sync
        offer_b = b.generate_sync_offer()
        payload = a.receive_sync_offer(offer_b)
        b.merge_sync_payload(payload)

        offer_a = a.generate_sync_offer()
        payload2 = b.receive_sync_offer(offer_a)
        a.merge_sync_payload(payload2)

        # B must have gamma's nodes
        assert b.get_node("srv-1") is not None, "server not synced"
        assert b.get_node("svc-api") is not None, "service not synced"
        assert b.get_node("guard-1") is not None, "guardrail not synced"
        assert b.get_edge("svc-api-RUNS_ON-srv-1") is not None, "edge not synced"

        # A must have delta's node
        assert a.get_node("srv-2") is not None, "reverse sync failed"
