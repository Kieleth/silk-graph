"""Silk GraphStore — Python tests for ontology-first scaffold."""

import json

import pytest

from silk import GraphStore

# -- Ontology fixtures -------------------------------------------------------

DEVOPS_ONTOLOGY = json.dumps(
    {
        "node_types": {
            "signal": {
                "description": "Something observed",
                "properties": {
                    "severity": {"value_type": "string", "required": True},
                },
            },
            "entity": {
                "description": "Something that exists",
                "properties": {
                    "ip": {"value_type": "string", "required": False},
                    "port": {"value_type": "int", "required": False},
                    "status": {"value_type": "string", "required": False},
                },
            },
            "rule": {"properties": {}},
            "plan": {"properties": {}},
            "action": {"properties": {}},
        },
        "edge_types": {
            "OBSERVES": {
                "source_types": ["signal"],
                "target_types": ["entity"],
                "properties": {},
            },
            "TRIGGERS": {
                "source_types": ["signal"],
                "target_types": ["rule"],
                "properties": {},
            },
            "RUNS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
            "PRODUCES": {
                "source_types": ["action"],
                "target_types": ["signal"],
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
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        assert store.len() == 1  # genesis entry
        assert len(store.heads()) == 1
        assert store.clock_time() == 1

    def test_genesis_contains_ontology(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        genesis_hash = store.heads()[0]
        entry = store.get(genesis_hash)
        payload = json.loads(entry["payload"])
        assert payload["op"] == "define_ontology"
        assert "signal" in payload["ontology"]["node_types"]
        assert "OBSERVES" in payload["ontology"]["edge_types"]

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
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        recovered = json.loads(store.ontology_json())
        assert "signal" in recovered["node_types"]
        assert "OBSERVES" in recovered["edge_types"]

    def test_node_type_names(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        names = store.node_type_names()
        assert set(names) == {"signal", "entity", "rule", "plan", "action"}

    def test_edge_type_names(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        names = store.edge_type_names()
        assert set(names) == {"OBSERVES", "TRIGGERS", "RUNS_ON", "PRODUCES"}


# -- Node validation tests ---------------------------------------------------


class TestNodeValidation:
    def test_add_valid_node(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        h = store.add_node("srv-1", "entity", "Server", {"ip": "10.0.0.1"})
        assert len(h) == 64
        assert store.len() == 2  # genesis + node

    def test_add_node_unknown_type_rejected(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        with pytest.raises(ValueError, match="unknown node type"):
            store.add_node("x", "potato", "Bad")

    def test_add_node_missing_required_property(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        with pytest.raises(ValueError, match="requires property"):
            store.add_node("s1", "signal", "Alert")  # missing severity

    def test_add_node_wrong_property_type(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        with pytest.raises(ValueError, match="expects"):
            store.add_node("s1", "signal", "Alert", {"severity": 42})  # int, not string

    def test_add_node_unknown_property_accepted(self):
        """D-026: unknown properties are accepted without validation."""
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("s1", "signal", "Alert", {"severity": "high", "bogus": True})
        node = store.get_node("s1")
        assert node["properties"]["bogus"] is True

    def test_add_node_optional_property_absent(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        h = store.add_node("srv-1", "entity", "Server")  # ip/port/status all optional
        assert len(h) == 64

    def test_add_all_ontology_node_types(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        for nt in ("entity", "rule", "plan", "action"):
            h = store.add_node(f"n-{nt}", nt, f"Test {nt}")
            assert len(h) == 64
        h = store.add_node("n-signal", "signal", "Alert", {"severity": "low"})
        assert len(h) == 64


# -- Edge validation tests ---------------------------------------------------


class TestEdgeValidation:
    def test_add_valid_edge(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("svc-1", "entity", "Service")
        store.add_node("srv-1", "entity", "Server")
        h = store.add_edge("e1", "RUNS_ON", "svc-1", "srv-1")
        assert len(h) == 64

    def test_add_edge_unknown_type_rejected(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        with pytest.raises(ValueError, match="unknown edge type"):
            store.add_edge("e1", "FLIES_TO", "a", "b")

    def test_add_edge_invalid_source_type(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("srv", "entity", "Server")
        store.add_node("rule", "rule", "Rule")
        # OBSERVES requires source=signal, not entity
        with pytest.raises(ValueError, match="cannot have source type"):
            store.add_edge("e1", "OBSERVES", "srv", "rule")

    def test_add_edge_invalid_target_type(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("sig", "signal", "Alert", {"severity": "high"})
        store.add_node("act", "action", "Deploy")
        # OBSERVES requires target=entity, not action
        with pytest.raises(ValueError, match="cannot have target type"):
            store.add_edge("e1", "OBSERVES", "sig", "act")

    def test_add_edge_source_not_found(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("srv", "entity", "Server")
        with pytest.raises(ValueError, match="source node.*not found"):
            store.add_edge("e1", "RUNS_ON", "ghost", "srv")

    def test_add_edge_target_not_found(self):
        store = GraphStore("node-a", DEVOPS_ONTOLOGY)
        store.add_node("srv", "entity", "Server")
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
