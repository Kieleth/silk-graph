"""Silk GraphStore — Python tests for ontology-first scaffold."""

import json

import pytest

from silk import GraphStore

# -- Ontology fixtures -------------------------------------------------------

SAMPLE_ONTOLOGY = json.dumps(
    {
        "node_types": {
            "alert": {
                "description": "A notification event",
                "properties": {
                    "severity": {"value_type": "string", "required": True},
                },
            },
            "server": {
                "description": "A compute resource",
                "properties": {
                    "ip": {"value_type": "string", "required": False},
                    "port": {"value_type": "int", "required": False},
                    "status": {"value_type": "string", "required": False},
                },
            },
            "service": {"properties": {}},
            "config": {"properties": {}},
            "deployment": {"properties": {}},
        },
        "edge_types": {
            "MONITORS": {
                "source_types": ["alert"],
                "target_types": ["server"],
                "properties": {},
            },
            "NOTIFIES": {
                "source_types": ["alert"],
                "target_types": ["service"],
                "properties": {},
            },
            "RUNS_ON": {
                "source_types": ["server"],
                "target_types": ["server"],
                "properties": {},
            },
            "DEPLOYS": {
                "source_types": ["deployment"],
                "target_types": ["alert"],
                "properties": {},
            },
        },
    }
)

MINIMAL_ONTOLOGY = json.dumps(
    {
        "node_types": {
            "thing": {"properties": {}},
        },
        "edge_types": {
            "LINKS": {
                "source_types": ["thing"],
                "target_types": ["thing"],
                "properties": {},
            },
        },
    }
)


# -- Genesis & ontology tests ------------------------------------------------


class TestGenesis:
    def test_store_starts_with_genesis(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        assert store.len() == 1  # genesis entry
        assert len(store.heads()) == 1
        ct = store.clock_time()
        assert isinstance(ct, tuple) and len(ct) == 2  # (physical_ms, logical)

    def test_genesis_contains_ontology(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        genesis_hash = store.heads()[0]
        entry = store.get(genesis_hash)
        payload = json.loads(entry["payload"])
        assert payload["op"] == "define_ontology"
        assert "alert" in payload["ontology"]["node_types"]
        assert "MONITORS" in payload["ontology"]["edge_types"]

    def test_invalid_ontology_json_raises(self):
        with pytest.raises(ValueError, match="invalid ontology JSON"):
            GraphStore("node-a", "not json")

    def test_inconsistent_ontology_raises(self):
        bad = json.dumps(
            {
                "node_types": {"entity": {"properties": {}}},
                "edge_types": {
                    "LINKS": {
                        "source_types": ["ghost"],  # doesn't exist
                        "target_types": ["entity"],
                        "properties": {},
                    },
                },
            }
        )
        with pytest.raises(ValueError, match="ontology validation failed"):
            GraphStore("node-a", bad)

    def test_ontology_json_roundtrip(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        recovered = json.loads(store.ontology_json())
        assert "alert" in recovered["node_types"]
        assert "MONITORS" in recovered["edge_types"]

    def test_node_type_names(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        names = store.node_type_names()
        assert set(names) == {"alert", "server", "service", "config", "deployment"}

    def test_edge_type_names(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        names = store.edge_type_names()
        assert set(names) == {"MONITORS", "NOTIFIES", "RUNS_ON", "DEPLOYS"}


# -- Node validation tests ---------------------------------------------------


class TestNodeValidation:
    def test_add_valid_node(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        h = store.add_node("srv-1", "server", "Server", {"ip": "10.0.0.1"})
        assert len(h) == 64
        assert store.len() == 2  # genesis + node

    def test_add_node_unknown_type_rejected(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        with pytest.raises(ValueError, match="unknown node type"):
            store.add_node("x", "potato", "Bad")

    def test_add_node_missing_required_property(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        with pytest.raises(ValueError, match="requires property"):
            store.add_node("a1", "alert", "Alert")  # missing severity

    def test_add_node_wrong_property_type(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        with pytest.raises(ValueError, match="expects"):
            store.add_node("a1", "alert", "Alert", {"severity": 42})  # int, not string

    def test_add_node_unknown_property_accepted(self):
        """D-026: unknown properties are accepted without validation."""
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("a1", "alert", "Alert", {"severity": "high", "bogus": True})
        node = store.get_node("a1")
        assert node["properties"]["bogus"] is True

    def test_add_node_optional_property_absent(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        h = store.add_node("srv-1", "server", "Server")  # ip/port/status all optional
        assert len(h) == 64

    def test_add_all_ontology_node_types(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        for nt in ("server", "service", "config", "deployment"):
            h = store.add_node(f"n-{nt}", nt, f"Test {nt}")
            assert len(h) == 64
        h = store.add_node("n-alert", "alert", "Alert", {"severity": "low"})
        assert len(h) == 64


# -- Edge validation tests ---------------------------------------------------


class TestEdgeValidation:
    def test_add_valid_edge(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("svc-1", "server", "Server A")
        store.add_node("srv-1", "server", "Server B")
        h = store.add_edge("e1", "RUNS_ON", "svc-1", "srv-1")
        assert len(h) == 64

    def test_add_edge_unknown_type_rejected(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("a", "server", "A")
        store.add_node("b", "server", "B")
        with pytest.raises(ValueError, match="unknown edge type"):
            store.add_edge("e1", "FLIES_TO", "a", "b")

    def test_add_edge_invalid_source_type(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("srv", "server", "Server")
        store.add_node("svc", "service", "Service")
        # MONITORS requires source=alert, not server
        with pytest.raises(ValueError, match="cannot have source type"):
            store.add_edge("e1", "MONITORS", "srv", "svc")

    def test_add_edge_invalid_target_type(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("a1", "alert", "Alert", {"severity": "high"})
        store.add_node("dep", "deployment", "Deploy")
        # MONITORS requires target=server, not deployment
        with pytest.raises(ValueError, match="cannot have target type"):
            store.add_edge("e1", "MONITORS", "a1", "dep")

    def test_add_edge_source_not_found(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("srv", "server", "Server")
        with pytest.raises(ValueError, match="source node.*not found"):
            store.add_edge("e1", "RUNS_ON", "ghost", "srv")

    def test_add_edge_target_not_found(self):
        store = GraphStore("node-a", SAMPLE_ONTOLOGY)
        store.add_node("srv", "server", "Server")
        with pytest.raises(ValueError, match="target node.*not found"):
            store.add_edge("e1", "RUNS_ON", "srv", "ghost")


# -- DAG structure tests ------------------------------------------------------


class TestDAGStructure:
    def test_heads_advance(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        genesis = store.heads()[0]
        h1 = store.add_node("n1", "thing", "First")
        assert store.heads() == [h1]
        assert store.heads() != [genesis]

    def test_causal_links(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        h1 = store.add_node("n1", "thing", "First")
        h2 = store.add_node("n2", "thing", "Second")
        entry = store.get(h2)
        assert h1 in entry["next"]

    def test_genesis_is_first_causal_link(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        genesis_hash = store.heads()[0]
        h1 = store.add_node("n1", "thing", "First")
        entry = store.get(h1)
        assert genesis_hash in entry["next"]

    def test_deterministic_hash(self):
        s1 = GraphStore("node-a", MINIMAL_ONTOLOGY)
        s2 = GraphStore("node-a", MINIMAL_ONTOLOGY)
        # Genesis entries should be identical
        assert s1.heads() == s2.heads()

    def test_get_missing_returns_none(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        assert store.get("aa" * 32) is None

    def test_invalid_hex_hash_raises(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        with pytest.raises(ValueError):
            store.get("not-hex")

    def test_remove_node_tracked(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        store.add_node("n1", "thing", "First")
        h = store.remove_node("n1")
        entry = store.get(h)
        payload = json.loads(entry["payload"])
        assert payload["op"] == "remove_node"

    def test_remove_edge(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        store.add_node("a", "thing", "A")
        store.add_node("b", "thing", "B")
        store.add_edge("e1", "LINKS", "a", "b")
        h = store.remove_edge("e1")
        entry = store.get(h)
        payload = json.loads(entry["payload"])
        assert payload["op"] == "remove_edge"

    def test_update_property(self):
        store = GraphStore("node-a", MINIMAL_ONTOLOGY)
        store.add_node("n1", "thing", "Node")
        h = store.update_property("n1", "key", "value")
        entry = store.get(h)
        payload = json.loads(entry["payload"])
        assert payload["op"] == "update_property"
        assert payload["value"] == "value"
