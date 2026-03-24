"""Partial sync — GraphView projections + filtered sync.

Demonstrates:
- GraphView: query-time filtering over the full graph
- Filtered sync: bandwidth-reduced transfer between peers
- Combined: filtered sync + GraphView for clean queries
"""
import platform
import sys
from silk import GraphStore, GraphView

ONTOLOGY = {
    "node_types": {
        "server": {"properties": {"region": {"value_type": "string"}}},
        "service": {"properties": {"status": {"value_type": "string"}}},
    },
    "edge_types": {
        "RUNS": {"source_types": ["server"], "target_types": ["service"], "properties": {}},
    },
}

print(f"Platform: {platform.platform()}")
print(f"Python  : {sys.version.split()[0]}")
print()

# Build infrastructure graph
store = GraphStore("ops", ONTOLOGY)
store.add_node("srv-eu", "server", "EU Server", {"region": "eu"})
store.add_node("srv-us", "server", "US Server", {"region": "us"})
store.add_node("svc-api", "service", "API", {"status": "up"})
store.add_node("svc-db", "service", "DB", {"status": "down"})
store.add_edge("e1", "RUNS", "srv-eu", "svc-api")
store.add_edge("e2", "RUNS", "srv-us", "svc-db")

print(f"Full graph: {len(store.all_nodes())} nodes, {len(store.all_edges())} edges")

# ── Approach 1: GraphView ──

server_view = GraphView(store, node_types=["server"])
print(f"\nServer view: {len(server_view.all_nodes())} nodes, {len(server_view.all_edges())} edges")
for n in server_view.all_nodes():
    print(f"  {n['node_id']}: {n['properties'].get('region')}")

service_view = GraphView(store, node_types=["service"])
print(f"\nService view: {len(service_view.all_nodes())} nodes")
for n in service_view.all_nodes():
    print(f"  {n['node_id']}: {n['properties'].get('status')}")

eu_view = GraphView(store, predicate=lambda n: n["properties"].get("region") == "eu")
print(f"\nEU view: {len(eu_view.all_nodes())} nodes")

# Full view (servers + services) — RUNS edges visible
full_view = GraphView(store, node_types=["server", "service"])
print(f"\nFull infra view: {len(full_view.all_nodes())} nodes, {len(full_view.all_edges())} edges")

# ── Approach 2: Filtered Sync ──

receiver = GraphStore("dashboard", ONTOLOGY)
offer = receiver.generate_sync_offer()
payload = store.receive_filtered_sync_offer(offer, ["server"])
receiver.merge_sync_payload(payload)

# Use GraphView on the receiver for clean queries
rv = GraphView(receiver, node_types=["server"])
print(f"\nReceiver (filtered sync + view): {len(rv.all_nodes())} server nodes")

assert len(server_view.all_nodes()) == 2
assert len(service_view.all_nodes()) == 2
assert len(eu_view.all_nodes()) == 1
assert len(rv.all_nodes()) == 2

print(f"\n✓ Partial sync works — views filter queries, filtered sync reduces transfer")
