"""Ring topology: 10 peers converge with no coordinator."""

import json
import platform
import sys
import time

from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "description": "A generic entity",
            "properties": {},
        },
    },
    "edge_types": {
        "LINKS": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
    },
})

PEER_COUNT = 10
NODES_PER_PEER = 100


def sync_pair(src: GraphStore, dst: GraphStore) -> int:
    offer = dst.generate_sync_offer()
    payload = src.receive_sync_offer(offer)
    return dst.merge_sync_payload(payload)


def ring_sync(peers: list[GraphStore]) -> int:
    """One round of ring sync (0->1, 1->2, ..., N-1->0). Returns total merged."""
    total = 0
    n = len(peers)
    for i in range(n):
        total += sync_pair(peers[i], peers[(i + 1) % n])
    return total


def main():
    print(f"Platform      : {platform.platform()}")
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Peers         : {PEER_COUNT}")
    print(f"Nodes per peer: {NODES_PER_PEER}")
    print(f"Total nodes   : {PEER_COUNT * NODES_PER_PEER}")
    print()

    # -- Create peers and share genesis --
    peers = [GraphStore(f"peer-{i}", ONTOLOGY) for i in range(PEER_COUNT)]

    # Bootstrap: chain sync so all share the same genesis
    for i in range(PEER_COUNT - 1):
        sync_pair(peers[i], peers[i + 1])

    # -- Each peer writes its own nodes --
    t_write = time.perf_counter()
    for i, peer in enumerate(peers):
        for j in range(NODES_PER_PEER):
            peer.add_node(f"p{i}-{j}", "entity", f"Peer {i} Node {j}")
    write_ms = (time.perf_counter() - t_write) * 1000

    counts = [len(p.all_nodes()) for p in peers]
    print(f"After writes: {counts} (each peer has {NODES_PER_PEER})")

    # -- Ring sync rounds until convergence --
    total_expected = PEER_COUNT * NODES_PER_PEER
    t_sync = time.perf_counter()
    rounds = 0

    while True:
        rounds += 1
        merged = ring_sync(peers)
        if merged == 0:
            break

    sync_ms = (time.perf_counter() - t_sync) * 1000

    counts = [len(p.all_nodes()) for p in peers]
    print(f"After sync  : {counts[0]} nodes on every peer")

    # -- Verify all peers have identical node sets --
    reference = sorted(n["node_id"] for n in peers[0].all_nodes())
    all_match = all(
        sorted(n["node_id"] for n in p.all_nodes()) == reference
        for p in peers[1:]
    )

    print()
    print(f"Write time : {write_ms:7.1f} ms ({PEER_COUNT * NODES_PER_PEER} nodes)")
    print(f"Sync time  : {sync_ms:7.1f} ms ({rounds} ring rounds)")
    print()

    ok = all(c == total_expected for c in counts) and all_match
    mark = "\u2713" if ok else "\u2717"
    print(f"{mark} {PEER_COUNT} peers converged in {rounds} rounds "
          f"\u2014 no leader, no election, no coordinator")


if __name__ == "__main__":
    main()
