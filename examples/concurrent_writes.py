"""Concurrent writes: per-property LWW merges without data loss."""

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


def sync_pair(src: GraphStore, dst: GraphStore) -> int:
    offer = dst.generate_sync_offer()
    payload = src.receive_sync_offer(offer)
    return dst.merge_sync_payload(payload)


def sync_bidirectional(a: GraphStore, b: GraphStore):
    sync_pair(a, b)
    sync_pair(b, a)


def main():
    print(f"Platform: {platform.platform()}")
    print(f"Python  : {sys.version.split()[0]}")
    print()

    store_a = GraphStore("store-a", ONTOLOGY)
    store_b = GraphStore("store-b", ONTOLOGY)

    # -- Both stores create the same node (sync first to share it) --
    store_a.add_node("server-1", "entity", "Server 1", {"status": "healthy"})
    sync_bidirectional(store_a, store_b)

    print("Initial state:")
    print(f"  server-1 = {store_a.get_node('server-1')['properties']}")
    print()

    # -- Concurrent conflicting + non-conflicting writes --
    # A: changes status (conflicting with B)
    store_a.update_property("server-1", "status", "healthy")

    # B: changes status (conflict) AND adds a new property (no conflict)
    store_b.update_property("server-1", "status", "degraded")
    store_b.update_property("server-1", "location", "eu-west")

    print("Before sync (diverged):")
    print(f"  Store A: status={store_a.get_node('server-1')['properties'].get('status')}, "
          f"location={store_a.get_node('server-1')['properties'].get('location', '<absent>')}")
    print(f"  Store B: status={store_b.get_node('server-1')['properties'].get('status')}, "
          f"location={store_b.get_node('server-1')['properties'].get('location', '<absent>')}")
    print()

    # -- Sync --
    t0 = time.perf_counter()
    sync_bidirectional(store_a, store_b)
    sync_ms = (time.perf_counter() - t0) * 1000

    node_a = store_a.get_node("server-1")
    node_b = store_b.get_node("server-1")
    props_a = node_a["properties"]
    props_b = node_b["properties"]

    print("After sync (converged):")
    print(f"  Store A: status={props_a.get('status')}, location={props_a.get('location')}")
    print(f"  Store B: status={props_b.get('status')}, location={props_b.get('location')}")
    print()

    # -- Verify convergence --
    status_match = props_a["status"] == props_b["status"]
    location_present = "location" in props_a and "location" in props_b
    location_match = props_a.get("location") == props_b.get("location") == "eu-west"

    print(f"Sync time: {sync_ms:.2f} ms")
    print(f"  status converged (LWW winner): {props_a['status']} {'[\u2713]' if status_match else '[\u2717]'}")
    print(f"  location preserved (no conflict): {props_a.get('location')} {'[\u2713]' if location_match else '[\u2717]'}")
    print()

    ok = status_match and location_present and location_match
    mark = "\u2713" if ok else "\u2717"
    print(f"{mark} Concurrent writes merged \u2014 no data loss for non-conflicting properties")


if __name__ == "__main__":
    main()
