"""Offline-first sync: two peers write independently, then converge."""

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

NODE_COUNT = 500


def sync_pair(src: GraphStore, dst: GraphStore) -> int:
    """Sync src -> dst. Returns entries merged."""
    offer = dst.generate_sync_offer()
    payload = src.receive_sync_offer(offer)
    return dst.merge_sync_payload(payload)


def main():
    print(f"Platform : {platform.platform()}")
    print(f"Python   : {sys.version.split()[0]}")
    print(f"Nodes/dev: {NODE_COUNT}")
    print()

    # -- Create two independent stores --
    dev_a = GraphStore("device-a", ONTOLOGY)
    dev_b = GraphStore("device-b", ONTOLOGY)

    # -- Offline writes --
    t0 = time.perf_counter()
    for i in range(NODE_COUNT):
        dev_a.add_node(f"a-{i}", "entity", f"Doc A-{i}")
    for i in range(NODE_COUNT):
        dev_b.add_node(f"b-{i}", "entity", f"Doc B-{i}")
    write_ms = (time.perf_counter() - t0) * 1000

    count_a = len(dev_a.all_nodes())
    count_b = len(dev_b.all_nodes())
    print(f"Before sync : Device A = {count_a} nodes, Device B = {count_b} nodes")
    assert count_a == NODE_COUNT
    assert count_b == NODE_COUNT

    # -- Sync both directions --
    t1 = time.perf_counter()
    sync_pair(dev_a, dev_b)  # A -> B
    sync_pair(dev_b, dev_a)  # B -> A
    sync_ms = (time.perf_counter() - t1) * 1000

    count_a = len(dev_a.all_nodes())
    count_b = len(dev_b.all_nodes())
    print(f"After sync  : Device A = {count_a} nodes, Device B = {count_b} nodes")
    assert count_a == NODE_COUNT * 2
    assert count_b == NODE_COUNT * 2

    # -- Verify identical node sets --
    ids_a = sorted(n["node_id"] for n in dev_a.all_nodes())
    ids_b = sorted(n["node_id"] for n in dev_b.all_nodes())
    assert ids_a == ids_b

    print()
    print(f"Write time : {write_ms:7.1f} ms ({NODE_COUNT * 2} nodes)")
    print(f"Sync time  : {sync_ms:7.1f} ms (bidirectional)")
    print()

    ok = count_a == count_b == NODE_COUNT * 2
    mark = "\u2713" if ok else "\u2717"
    print(f"{mark} Both devices converged \u2014 no coordinator, no server")


if __name__ == "__main__":
    main()
