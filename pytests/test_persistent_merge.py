"""TDD: Persistent store must survive merge + reopen.

The bug: merge_sync_payload inserts entries into the in-memory oplog
but does NOT write them to redb. Reopening the store loses all merged data.
"""

import json
import os
import tempfile

import pytest

from silk import GraphStore


ONTOLOGY = json.dumps(
    {
        "node_types": {
            "entity": {
                "description": "A thing",
                "properties": {},
            },
        },
        "edge_types": {},
    }
)


class TestPersistentMerge:
    def test_merged_entries_survive_reopen(self):
        """Entries received via sync must persist to redb and survive reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "peer_b.silk")

            # Peer A: in-memory, writes some nodes.
            store_a = GraphStore("inst-a", ONTOLOGY)
            store_a.add_node("n1", "entity", "Node 1")
            store_a.add_node("n2", "entity", "Node 2")

            # Peer B: persistent, starts empty.
            store_b = GraphStore("inst-b", ONTOLOGY, path=db_path)
            assert store_b.len() == 1  # just genesis

            # Sync A → B.
            offer_b = store_b.generate_sync_offer()
            payload = store_a.receive_sync_offer(offer_b)
            merged = store_b.merge_sync_payload(payload)
            assert merged >= 2

            # B should have the nodes now.
            assert store_b.get_node("n1") is not None
            assert store_b.get_node("n2") is not None
            len_before = store_b.len()

            # Drop B and reopen from disk.
            del store_b
            store_b2 = GraphStore.open(db_path)

            # Merged entries must survive.
            assert store_b2.len() == len_before, f"reopen lost entries: {store_b2.len()} vs {len_before}"
            assert store_b2.get_node("n1") is not None, "n1 lost after reopen"
            assert store_b2.get_node("n2") is not None, "n2 lost after reopen"

    def test_merged_entries_heads_survive_reopen(self):
        """DAG heads must be correct after merge + reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "peer_b.silk")

            store_a = GraphStore("inst-a", ONTOLOGY)
            store_a.add_node("n1", "entity", "Node 1")

            store_b = GraphStore("inst-b", ONTOLOGY, path=db_path)

            # Sync.
            offer_b = store_b.generate_sync_offer()
            payload = store_a.receive_sync_offer(offer_b)
            store_b.merge_sync_payload(payload)

            heads_before = store_b.heads()

            # Reopen.
            del store_b
            store_b2 = GraphStore.open(db_path)

            assert set(store_b2.heads()) == set(heads_before), "heads changed after reopen"

    def test_merged_graph_queries_survive_reopen(self):
        """Graph queries on merged data must work after reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "peer_b.silk")

            store_a = GraphStore("inst-a", ONTOLOGY)
            store_a.add_node("a", "entity", "A")
            store_a.add_node("b", "entity", "B")

            store_b = GraphStore("inst-b", ONTOLOGY, path=db_path)

            offer_b = store_b.generate_sync_offer()
            payload = store_a.receive_sync_offer(offer_b)
            store_b.merge_sync_payload(payload)

            # Reopen.
            del store_b
            store_b2 = GraphStore.open(db_path)

            nodes = store_b2.all_nodes()
            node_ids = {n["node_id"] for n in nodes}
            assert node_ids == {"a", "b"}, f"got {node_ids}"

    def test_snapshot_into_persistent_store_survives_reopen(self):
        """from_snapshot doesn't support persistent mode, but snapshot bytes
        merged into a persistent store via merge_sync_payload should persist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "new_peer.silk")

            # Source store with data.
            store_a = GraphStore("inst-a", ONTOLOGY)
            store_a.add_node("x", "entity", "X")
            store_a.add_node("y", "entity", "Y")

            # New persistent peer, bootstrap via sync (not snapshot).
            store_new = GraphStore("inst-new", ONTOLOGY, path=db_path)
            offer = store_new.generate_sync_offer()
            payload = store_a.receive_sync_offer(offer)
            store_new.merge_sync_payload(payload)

            assert store_new.get_node("x") is not None
            assert store_new.get_node("y") is not None

            # Reopen.
            del store_new
            store_new2 = GraphStore.open(db_path)

            assert store_new2.get_node("x") is not None, "x lost"
            assert store_new2.get_node("y") is not None, "y lost"
