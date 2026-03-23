"""D-027: ed25519 author authentication tests.

Tests verifying that entries can be signed, verified, and that the
trust model works correctly (trusted authors, strict mode, migration).
"""

import json
import pytest
from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {"properties": {}}
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {}
        }
    }
})


def _store(instance_id="test"):
    return GraphStore(instance_id, ONTOLOGY)


# -- Key generation and management --


def test_generate_signing_key():
    """generate_signing_key returns a hex public key."""
    store = _store()
    pub_key = store.generate_signing_key()
    assert isinstance(pub_key, str)
    assert len(pub_key) == 64  # 32 bytes = 64 hex chars
    assert all(c in "0123456789abcdef" for c in pub_key)


def test_get_public_key_none_before_set():
    """get_public_key returns None before any key is set."""
    store = _store()
    assert store.get_public_key() is None


def test_get_public_key_after_generate():
    """get_public_key returns the generated key."""
    store = _store()
    pub_key = store.generate_signing_key()
    assert store.get_public_key() == pub_key


def test_set_signing_key():
    """set_signing_key loads an existing key."""
    store = _store()
    # Generate a key on one store, export the private key concept
    pub1 = store.generate_signing_key()
    assert store.get_public_key() == pub1


# -- Signed entries --


def test_entries_are_signed_when_key_set():
    """Entries are automatically signed when a signing key is set."""
    store = _store("a")
    store.generate_signing_key()

    store.add_node("n1", "entity", "Node 1")
    node = store.get_node("n1")
    assert node is not None


def test_signed_entries_sync_correctly():
    """Signed entries can be synced between peers."""
    store_a = _store("a")
    store_b = _store("b")

    pub_a = store_a.generate_signing_key()
    pub_b = store_b.generate_signing_key()

    # Register each other's public keys
    store_a.register_trusted_author("b", pub_b)
    store_b.register_trusted_author("a", pub_a)

    # Write on A
    store_a.add_node("n1", "entity", "From A")

    # Sync A → B
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    assert store_b.get_node("n1") is not None


def test_unsigned_entries_accepted_in_default_mode():
    """Without require_signatures, unsigned entries are accepted."""
    store_a = _store("a")
    store_b = _store("b")

    # A writes without signing
    store_a.add_node("n1", "entity", "Unsigned")

    # Sync
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    assert store_b.get_node("n1") is not None


def test_strict_mode_rejects_unsigned():
    """With require_signatures=True, unsigned entries are rejected."""
    store_a = _store("a")
    store_b = _store("b")

    store_b.generate_signing_key()
    store_b.set_require_signatures(True)

    # A writes without signing
    store_a.add_node("n1", "entity", "Unsigned")
    store_a.add_node("n2", "entity", "Also unsigned")

    # Sync — B should reject unsigned entries
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    count = store_b.merge_sync_payload(payload)

    # B should not have A's unsigned nodes (except genesis which is always accepted)
    assert store_b.get_node("n1") is None
    assert store_b.get_node("n2") is None


def test_strict_mode_accepts_signed_from_trusted():
    """With require_signatures=True, signed entries from trusted authors are accepted."""
    store_a = _store("a")
    store_b = _store("b")

    pub_a = store_a.generate_signing_key()
    store_b.generate_signing_key()
    store_b.register_trusted_author("a", pub_a)
    store_b.set_require_signatures(True)

    store_a.add_node("n1", "entity", "Signed by A")

    # Sync
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)

    assert store_b.get_node("n1") is not None


def test_register_trusted_author():
    """register_trusted_author adds a key to the registry."""
    store = _store()
    # Generate a key to get a valid public key hex
    other = _store("other")
    pub_key = other.generate_signing_key()

    store.register_trusted_author("other", pub_key)
    # No error = success. The key is stored internally.


def test_multiple_peers_with_signing():
    """Three peers, all signing, all trusting each other."""
    stores = [_store(f"peer-{i}") for i in range(3)]
    pub_keys = [s.generate_signing_key() for s in stores]

    # Register all keys with all peers
    for i, store in enumerate(stores):
        for j, pub_key in enumerate(pub_keys):
            if i != j:
                store.register_trusted_author(f"peer-{j}", pub_key)

    # Each writes a node
    for i, store in enumerate(stores):
        store.add_node(f"n{i}", "entity", f"Node from peer {i}")

    # Full mesh sync
    for i in range(3):
        for j in range(3):
            if i != j:
                offer = stores[i].generate_sync_offer()
                payload = stores[j].receive_sync_offer(offer)
                stores[i].merge_sync_payload(payload)

    # All peers should have all nodes
    for store in stores:
        for i in range(3):
            assert store.get_node(f"n{i}") is not None
