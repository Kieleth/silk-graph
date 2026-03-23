"""R-05: Gossip Peer Selection — logarithmic fan-out for scalable sync.

Tests verifying peer registry and sync target selection.
"""

import json
import math
from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {"entity": {"properties": {}}},
    "edge_types": {}
})


def _store():
    return GraphStore("test", ONTOLOGY)


def test_register_and_list():
    store = _store()
    store.register_peer("a", "tcp://a:7701")
    store.register_peer("b", "tcp://b:7701")
    peers = store.list_peers()
    assert len(peers) == 2
    ids = {p["peer_id"] for p in peers}
    assert ids == {"a", "b"}


def test_unregister():
    store = _store()
    store.register_peer("a", "tcp://a:7701")
    assert store.unregister_peer("a") is True
    assert store.unregister_peer("a") is False
    assert len(store.list_peers()) == 0


def test_select_empty():
    store = _store()
    assert store.select_sync_targets() == []


def test_select_one_peer():
    store = _store()
    store.register_peer("only", "tcp://only:7701")
    targets = store.select_sync_targets()
    assert targets == ["only"]


def test_select_two_peers():
    store = _store()
    store.register_peer("a", "tcp://a:7701")
    store.register_peer("b", "tcp://b:7701")
    targets = store.select_sync_targets()
    assert len(targets) == 2
    assert set(targets) == {"a", "b"}


def test_select_logarithmic_100():
    """100 peers → ceil(ln(100) + 1) = 6 targets."""
    store = _store()
    for i in range(100):
        store.register_peer(f"p{i}", f"tcp://p{i}:7701")
    targets = store.select_sync_targets()
    expected = math.ceil(math.log(100) + 1)
    assert len(targets) == expected


def test_select_logarithmic_1000():
    """1000 peers → ceil(ln(1000) + 1) = 8 targets."""
    store = _store()
    for i in range(1000):
        store.register_peer(f"p{i}", f"tcp://p{i}:7701")
    targets = store.select_sync_targets()
    expected = math.ceil(math.log(1000) + 1)
    assert len(targets) == expected


def test_select_no_duplicates():
    store = _store()
    for i in range(50):
        store.register_peer(f"p{i}", f"tcp://p{i}:7701")
    targets = store.select_sync_targets()
    assert len(targets) == len(set(targets))


def test_select_all_valid():
    store = _store()
    ids = {f"p{i}" for i in range(50)}
    for pid in ids:
        store.register_peer(pid, f"tcp://{pid}:7701")
    targets = store.select_sync_targets()
    for t in targets:
        assert t in ids


def test_record_sync():
    store = _store()
    store.register_peer("a", "tcp://a:7701")
    peers = store.list_peers()
    assert peers[0]["last_seen_ms"] == 0
    store.record_sync("a")
    peers = store.list_peers()
    assert peers[0]["last_seen_ms"] > 0


def test_peer_info_structure():
    store = _store()
    store.register_peer("test-peer", "tcp://10.0.0.1:7701")
    peers = store.list_peers()
    assert len(peers) == 1
    p = peers[0]
    assert p["peer_id"] == "test-peer"
    assert p["address"] == "tcp://10.0.0.1:7701"
    assert isinstance(p["last_seen_ms"], int)
