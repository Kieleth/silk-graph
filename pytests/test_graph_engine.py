"""Silk GraphStore — Python tests for graph queries and engine algorithms."""

import json

import pytest

from silk import GraphStore

ONTOLOGY = json.dumps(
    {
        "node_types": {
            "entity": {"properties": {"status": {"value_type": "string", "required": False}}},
            "signal": {"properties": {}},
            "rule": {"properties": {}},
            "plan": {"properties": {}},
            "action": {"properties": {}},
        },
        "edge_types": {
            "DEPENDS_ON": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
            "TRIGGERS": {
                "source_types": ["signal"],
                "target_types": ["rule"],
                "properties": {},
            },
            "PRODUCES": {
                "source_types": ["rule", "plan", "action"],
                "target_types": ["plan", "action", "signal"],
                "properties": {},
            },
        },
    }
)


class TestGraphQueries:
    """Tests for materialized graph query API."""

    def test_get_node(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("s1", "entity", "Server 1", {"status": "alive"})
        node = store.get_node("s1")
        assert node is not None
        assert node["node_id"] == "s1"
        assert node["node_type"] == "entity"
        assert node["label"] == "Server 1"
        assert node["properties"]["status"] == "alive"

    def test_get_node_not_found(self):
        store = GraphStore("inst-1", ONTOLOGY)
        assert store.get_node("nonexistent") is None

    def test_get_edge(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_edge("e1", "DEPENDS_ON", "a", "b")
        edge = store.get_edge("e1")
        assert edge is not None
        assert edge["edge_type"] == "DEPENDS_ON"
        assert edge["source_id"] == "a"
        assert edge["target_id"] == "b"

    def test_query_by_type(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("s1", "entity", "S1")
        store.add_node("s2", "entity", "S2")
        store.add_node("sig1", "signal", "Alert")
        entities = store.query_nodes_by_type("entity")
        assert len(entities) == 2
        signals = store.query_nodes_by_type("signal")
        assert len(signals) == 1

    def test_query_by_property(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("s1", "entity", "S1", {"status": "alive"})
        store.add_node("s2", "entity", "S2", {"status": "dead"})
        alive = store.query_nodes_by_property("status", "alive")
        assert len(alive) == 1
        assert alive[0]["node_id"] == "s1"

    def test_all_nodes_excludes_removed(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("s1", "entity", "S1")
        store.add_node("s2", "entity", "S2")
        assert len(store.all_nodes()) == 2
        store.remove_node("s1")
        assert len(store.all_nodes()) == 1

    def test_all_edges_excludes_dangling(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_edge("e1", "DEPENDS_ON", "a", "b")
        assert len(store.all_edges()) == 1
        store.remove_node("b")
        # Edge still exists in oplog but invisible (dangling target).
        assert len(store.all_edges()) == 0

    def test_neighbors(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_node("c", "entity", "C")
        store.add_edge("ab", "DEPENDS_ON", "a", "b")
        store.add_edge("ac", "DEPENDS_ON", "a", "c")
        neighbors = store.neighbors("a")
        assert set(neighbors) == {"b", "c"}

    def test_outgoing_incoming_edges(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_edge("e1", "DEPENDS_ON", "a", "b")
        out = store.outgoing_edges("a")
        assert len(out) == 1
        assert out[0]["target_id"] == "b"
        inc = store.incoming_edges("b")
        assert len(inc) == 1
        assert inc[0]["source_id"] == "a"


class TestEngine:
    """Tests for graph algorithms exposed via Python."""

    def _build_chain(self):
        """A → B → C → D"""
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_node("c", "entity", "C")
        store.add_node("d", "entity", "D")
        store.add_edge("ab", "DEPENDS_ON", "a", "b")
        store.add_edge("bc", "DEPENDS_ON", "b", "c")
        store.add_edge("cd", "DEPENDS_ON", "c", "d")
        return store

    def test_bfs(self):
        store = self._build_chain()
        visited = store.bfs("a")
        assert visited == ["a", "b", "c", "d"]

    def test_bfs_depth_limit(self):
        store = self._build_chain()
        visited = store.bfs("a", max_depth=2)
        assert visited == ["a", "b", "c"]

    def test_bfs_edge_type_filter(self):
        store = self._build_chain()
        visited = store.bfs("a", edge_type="NONEXISTENT")
        assert visited == ["a"]  # only start node

    def test_shortest_path(self):
        store = self._build_chain()
        path = store.shortest_path("a", "d")
        assert path == ["a", "b", "c", "d"]

    def test_shortest_path_no_path(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        assert store.shortest_path("a", "b") is None

    def test_impact_analysis(self):
        store = self._build_chain()
        impact = store.impact_analysis("d")
        assert set(impact) == {"a", "b", "c", "d"}

    def test_subgraph(self):
        store = self._build_chain()
        result = store.subgraph("b", 1)
        assert "b" in result["nodes"]
        assert "a" in result["nodes"]
        assert "c" in result["nodes"]
        assert "d" not in result["nodes"]

    def test_pattern_match(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("sig1", "signal", "Alert")
        store.add_node("rule1", "rule", "Rule 1")
        store.add_node("plan1", "plan", "Plan 1")
        store.add_node("act1", "action", "Action 1")
        store.add_edge("e1", "TRIGGERS", "sig1", "rule1")
        store.add_edge("e2", "PRODUCES", "rule1", "plan1")
        store.add_edge("e3", "PRODUCES", "plan1", "act1")
        chains = store.pattern_match(["signal", "rule", "plan", "action"])
        assert len(chains) == 1
        assert chains[0] == ["sig1", "rule1", "plan1", "act1"]

    def test_topological_sort(self):
        store = self._build_chain()
        order = store.topological_sort()
        assert order is not None
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")
        assert order.index("c") < order.index("d")

    def test_cycle_detection(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("a", "entity", "A")
        store.add_node("b", "entity", "B")
        store.add_node("c", "entity", "C")
        store.add_edge("ab", "DEPENDS_ON", "a", "b")
        store.add_edge("bc", "DEPENDS_ON", "b", "c")
        store.add_edge("ca", "DEPENDS_ON", "c", "a")
        assert store.has_cycle() is True
        assert store.topological_sort() is None

    def test_no_cycle(self):
        store = self._build_chain()
        assert store.has_cycle() is False
