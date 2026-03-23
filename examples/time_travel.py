"""R-06: Time-Travel Queries — look at the graph at any point in the past.

Demonstrates:
- Writing data over time
- Querying historical state with store.as_of()
- GraphSnapshot is read-only
"""
import platform
import sys
import time
from silk import GraphStore

ONTOLOGY = {
    "node_types": {
        "server": {"properties": {"status": {"value_type": "string"}}},
    },
    "edge_types": {}
}

print(f"Platform: {platform.platform()}")
print(f"Python  : {sys.version.split()[0]}")
print()

store = GraphStore("ops-team", ONTOLOGY)

# Phase 1: healthy infrastructure
store.add_node("srv-1", "server", "Production", {"status": "healthy"})
store.add_node("srv-2", "server", "Staging", {"status": "healthy"})
t_healthy = store.clock_time()
print(f"Phase 1: 2 servers, both healthy (clock: {t_healthy})")

time.sleep(0.01)

# Phase 2: incident — srv-1 goes down
store.update_property("srv-1", "status", "down")
t_incident = store.clock_time()
print(f"Phase 2: srv-1 went down (clock: {t_incident})")

time.sleep(0.01)

# Phase 3: recovery
store.update_property("srv-1", "status", "recovered")
t_recovered = store.clock_time()
print(f"Phase 3: srv-1 recovered (clock: {t_recovered})")

# Time-travel queries
print()
snap_healthy = store.as_of(t_healthy[0], t_healthy[1])
snap_incident = store.as_of(t_incident[0], t_incident[1])
snap_recovered = store.as_of(t_recovered[0], t_recovered[1])

srv1_healthy = snap_healthy.get_node("srv-1")
srv1_incident = snap_incident.get_node("srv-1")
srv1_recovered = snap_recovered.get_node("srv-1")

print(f"srv-1 at phase 1: {srv1_healthy['properties']['status']}")
print(f"srv-1 at phase 2: {srv1_incident['properties']['status']}")
print(f"srv-1 at phase 3: {srv1_recovered['properties']['status']}")

assert srv1_healthy["properties"]["status"] == "healthy"
assert srv1_incident["properties"]["status"] == "down"
assert srv1_recovered["properties"]["status"] == "recovered"

print(f"\n✓ Time-travel works — same node, three different historical states")
