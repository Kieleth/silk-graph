"""D-027: ed25519 signing — authenticated, tamper-proof knowledge graphs.

Demonstrates:
- Key generation and exchange
- Auto-signed entries
- Trust registry
- Strict mode (reject unsigned entries)
"""
import json
import platform
import sys
import time

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

print(f"Platform: {platform.platform()}")
print(f"Python  : {sys.version.split()[0]}")
print()

# 1. Create two peers with signing keys
store_a = GraphStore("peer-a", ONTOLOGY)
store_b = GraphStore("peer-b", ONTOLOGY)

pub_a = store_a.generate_signing_key()
pub_b = store_b.generate_signing_key()

print(f"Peer A public key: {pub_a[:16]}...")
print(f"Peer B public key: {pub_b[:16]}...")

# 2. Register each other as trusted
store_a.register_trusted_author("peer-b", pub_b)
store_b.register_trusted_author("peer-a", pub_a)

# 3. Enable strict mode on both
store_a.set_require_signatures(True)
store_b.set_require_signatures(True)

# 4. Write signed entries
store_a.add_node("doc-1", "entity", "Signed by A")
store_b.add_node("doc-2", "entity", "Signed by B")

# 5. Sync — only signed entries from trusted authors are accepted
t0 = time.perf_counter()
for _ in range(2):  # two rounds for full convergence
    offer = store_a.generate_sync_offer()
    payload = store_b.receive_sync_offer(offer)
    store_a.merge_sync_payload(payload)

    offer = store_b.generate_sync_offer()
    payload = store_a.receive_sync_offer(offer)
    store_b.merge_sync_payload(payload)
sync_ms = (time.perf_counter() - t0) * 1000

print(f"\nAfter sync:")
print(f"  Peer A nodes: {len(store_a.all_nodes())}")
print(f"  Peer B nodes: {len(store_b.all_nodes())}")
print(f"  Sync time: {sync_ms:.1f} ms")

assert store_a.get_node("doc-1") is not None
assert store_a.get_node("doc-2") is not None
assert store_b.get_node("doc-1") is not None
assert store_b.get_node("doc-2") is not None

# 6. Demonstrate rejection: untrusted peer
store_c = GraphStore("peer-c", ONTOLOGY)
store_c.add_node("doc-3", "entity", "Unsigned by C")

offer = store_c.generate_sync_offer()
payload = store_a.receive_sync_offer(offer)
store_c.merge_sync_payload(payload)

offer = store_a.generate_sync_offer()
payload = store_c.receive_sync_offer(offer)
store_a.merge_sync_payload(payload)

# A should NOT have C's unsigned entry (strict mode)
assert store_a.get_node("doc-3") is None

print(f"\nUntrusted peer C's unsigned entry: rejected by A [correct]")
print(f"\n✓ Authenticated sync — only trusted, signed entries accepted")
