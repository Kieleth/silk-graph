"""R-07: Query Builder — fluent graph queries.

Demonstrates:
- Building an infrastructure graph
- Querying with .nodes(), .where(), .follow()
- Finding down services on active servers
- Extension point for custom engines
"""
import platform
import sys
from silk import GraphStore, Query

ONTOLOGY = {
    "node_types": {
        "server": {"properties": {"status": {"value_type": "string"}, "region": {"value_type": "string"}}},
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
store.add_node("srv-1", "server", "Prod EU", {"status": "active", "region": "eu-west"})
store.add_node("srv-2", "server", "Prod US", {"status": "active", "region": "us-east"})
store.add_node("srv-3", "server", "Staging", {"status": "standby", "region": "eu-west"})
store.add_node("svc-api", "service", "API", {"status": "up"})
store.add_node("svc-web", "service", "Web", {"status": "up"})
store.add_node("svc-db", "service", "Database", {"status": "down"})
store.add_edge("e1", "RUNS", "srv-1", "svc-api")
store.add_edge("e2", "RUNS", "srv-1", "svc-web")
store.add_edge("e3", "RUNS", "srv-2", "svc-db")

print("Graph: 3 servers, 3 services, 3 RUNS edges")
print()

# Query 1: All active servers
active = Query(store).nodes("server").where(status="active").collect_ids()
print(f"Active servers: {active}")

# Query 2: EU servers
eu = Query(store).nodes("server").where(region="eu-west").collect_ids()
print(f"EU servers: {eu}")

# Query 3: Services on srv-1
on_srv1 = (
    Query(store).nodes("server")
    .where_fn(lambda n: n["node_id"] == "srv-1")
    .follow("RUNS")
    .collect_ids()
)
print(f"Services on srv-1: {on_srv1}")

# Query 4: THE query — down services on active servers
down = (
    Query(store)
    .nodes("server")
    .where(status="active")
    .follow("RUNS")
    .where(status="down")
    .collect()
)
print(f"\nDown services on active servers: {[s['node_id'] for s in down]}")
assert len(down) == 1
assert down[0]["node_id"] == "svc-db"

print(f"\n✓ Query builder works — found {len(down)} down service on active servers")
