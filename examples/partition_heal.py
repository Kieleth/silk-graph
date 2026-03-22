"""Partition healing: three peers diverge, then converge via mesh sync."""

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

NODES_PER_PEER = 200


def sync_pair(src: GraphStore, dst: GraphStore) -> int:
    """Sync src -> dst. Returns entries merged."""
    offer = dst.generate_sync_offer()
    payload = src.receive_sync_offer(offer)
    return dst.merge_sync_payload(payload)


def sync_bidirectional(a: GraphStore, b: GraphStore):
    """Full bidirectional sync between two peers."""
    sync_pair(a, b)
    sync_pair(b, a)


def main():
    print(f"Platform      : {platform.platform()}")
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Nodes per peer: {NODES_PER_PEER}")
    print()

    peers = {
        name: GraphStore(name, ONTOLOGY)
        for name in ("peer-a", "peer-b", "peer-c")
    }
    a, b, c = peers["peer-a"], peers["peer-b"], peers["peer-c"]

    # -- Initial sync (share genesis) --
    sync_bidirectional(a, b)
    sync_bidirectional(b, c)

    # -- Simulate partition: each peer writes independently --
    for i in range(NODES_PER_PEER):
        a.add_node(f"a-{i}", "entity", f"Node A-{i}")
        b.add_node(f"b-{i}", "entity", f"Node B-{i}")
        c.add_node(f"c-{i}", "entity", f"Node C-{i}")

    total_expected = NODES_PER_PEER * 3
    counts = {name: len(s.all_nodes()) for name, s in peers.items()}
    print(f"After partition: A={counts['peer-a']}, B={counts['peer-b']}, C={counts['peer-c']} "
          f"({total_expected} total, no overlap)")

    # -- Heal: full mesh sync --
    t0 = time.perf_counter()
    sync_bidirectional(a, b)
    sync_bidirectional(b, c)
    sync_bidirectional(a, c)
    heal_ms = (time.perf_counter() - t0) * 1000

    counts = {name: len(s.all_nodes()) for name, s in peers.items()}
    print(f"After healing : A={counts['peer-a']}, B={counts['peer-b']}, C={counts['peer-c']}")

    # -- Verify identical node sets --
    ids = {
        name: sorted(n["node_id"] for n in s.all_nodes())
        for name, s in peers.items()
    }
    assert ids["peer-a"] == ids["peer-b"] == ids["peer-c"]

    ok = all(c == total_expected for c in counts.values())
    mark = "\u2713" if ok else "\u2717"
    print()
    print(f"Heal time: {heal_ms:.1f} ms")
    print(f"{mark} All three peers converged after partition heal")


if __name__ == "__main__":
    main()
