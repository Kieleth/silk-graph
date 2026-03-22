"""Silk GraphStore — Python tests for persistence (redb) and entries_since."""

import json
import os
import tempfile

import pytest

from silk import GraphStore

ONTOLOGY = json.dumps(
    {
        "node_types": {
            "entity": {
                "properties": {
                    "status": {"value_type": "string", "required": False},
                },
            },
        },
        "edge_types": {
            "LINKS": {
                "source_types": ["entity"],
                "target_types": ["entity"],
                "properties": {},
            },
        },
    }
)


class TestPersistence:
    """Tests for redb-backed persistent stores."""

    def test_persistent_store_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")
            assert not os.path.exists(path)
            store = GraphStore("inst-1", ONTOLOGY, path)
            assert os.path.exists(path)
            assert store.len() == 1  # genesis

    def test_persistent_store_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")

            # Create and populate.
            store = GraphStore("inst-1", ONTOLOGY, path)
            store.add_node("n1", "entity", "Node 1")
            store.add_node("n2", "entity", "Node 2")
            assert store.len() == 3  # genesis + 2 nodes
            del store

            # Reopen — state should be preserved.
            store2 = GraphStore.open(path)
            assert store2.len() == 3
            assert len(store2.heads()) == 1

    def test_persistent_store_preserves_heads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")

            store = GraphStore("inst-1", ONTOLOGY, path)
            h1 = store.add_node("n1", "entity", "Node 1")
            h2 = store.add_node("n2", "entity", "Node 2")
            heads_before = store.heads()
            del store

            store2 = GraphStore.open(path)
            # Should have single head (linear chain).
            assert len(store2.heads()) == 1
            # The head should be the last entry appended.
            assert store2.heads()[0] == h2

    def test_persistent_store_get_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")

            store = GraphStore("inst-1", ONTOLOGY, path)
            h1 = store.add_node("n1", "entity", "Node 1")
            del store

            store2 = GraphStore.open(path)
            entry = store2.get(h1)
            assert entry is not None
            assert entry["hash"] == h1

    def test_open_nonexistent_raises(self):
        with pytest.raises(IOError):
            GraphStore.open("/tmp/nonexistent_silk_test.redb")

    def test_in_memory_default_still_works(self):
        """In-memory mode (no path) should work identically to before."""
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("n1", "entity", "Node 1")
        assert store.len() == 2
        assert len(store.heads()) == 1


class TestEntriesSince:
    """Tests for the entries_since (delta) API."""

    def test_entries_since_none_returns_all(self):
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("n1", "entity", "Node 1")
        store.add_node("n2", "entity", "Node 2")
        entries = store.entries_since()
        assert len(entries) == 3  # genesis + 2 nodes

    def test_entries_since_hash_returns_delta(self):
        store = GraphStore("inst-1", ONTOLOGY)
        h1 = store.add_node("n1", "entity", "Node 1")
        h2 = store.add_node("n2", "entity", "Node 2")
        h3 = store.add_node("n3", "entity", "Node 3")

        delta = store.entries_since(h1)
        # Should return entries after h1: h2 and h3
        hashes = [e["hash"] for e in delta]
        assert len(hashes) == 2
        assert h2 in hashes
        assert h3 in hashes

    def test_entries_since_preserves_causal_order(self):
        store = GraphStore("inst-1", ONTOLOGY)
        h1 = store.add_node("n1", "entity", "Node 1")
        h2 = store.add_node("n2", "entity", "Node 2")
        h3 = store.add_node("n3", "entity", "Node 3")

        all_entries = store.entries_since()
        # Should be in causal order: genesis, h1, h2, h3
        hashes = [e["hash"] for e in all_entries]
        assert hashes.index(h1) < hashes.index(h2)
        assert hashes.index(h2) < hashes.index(h3)

    def test_entries_since_latest_returns_empty(self):
        store = GraphStore("inst-1", ONTOLOGY)
        h1 = store.add_node("n1", "entity", "Node 1")

        # entries_since the head itself should return empty delta
        delta = store.entries_since(h1)
        assert len(delta) == 0

    def test_entries_since_persistent(self):
        """entries_since should work on reopened persistent stores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.redb")
            store = GraphStore("inst-1", ONTOLOGY, path)
            h1 = store.add_node("n1", "entity", "Node 1")
            store.add_node("n2", "entity", "Node 2")
            del store

            store2 = GraphStore.open(path)
            delta = store2.entries_since(h1)
            assert len(delta) == 1  # only n2

    def test_entry_payload_is_json(self):
        """Each entry dict should have a 'payload' field that is valid JSON."""
        store = GraphStore("inst-1", ONTOLOGY)
        store.add_node("n1", "entity", "Node 1", {"status": "active"})
        entries = store.entries_since()
        for e in entries:
            assert "payload" in e
            parsed = json.loads(e["payload"])
            assert "op" in parsed
