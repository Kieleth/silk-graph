"""R-08: Epoch Compaction — compress the oplog into a checkpoint.

Demonstrates:
- Building up history (many writes)
- Compacting to a single checkpoint
- Verifying all data preserved
- Writing after compaction
"""
import platform
import sys
from silk import GraphStore

ONTOLOGY = {
    "node_types": {
        "entity": {"properties": {"status": {"value_type": "string"}}}
    },
    "edge_types": {
        "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
    }
}

print(f"Platform: {platform.platform()}")
print(f"Python  : {sys.version.split()[0]}")
print()

store = GraphStore("ops", ONTOLOGY)

# Build up history
for i in range(100):
    store.add_node(f"n{i}", "entity", f"Node {i}", {"status": "active"})
for i in range(99):
    store.add_edge(f"e{i}", "LINKS", f"n{i}", f"n{i+1}")

# Some updates (creates more oplog entries)
for i in range(50):
    store.update_property(f"n{i}", "status", "updated")

print(f"Before compaction:")
print(f"  Oplog entries: {store.len()}")
print(f"  Live nodes:    {len(store.all_nodes())}")
print(f"  Live edges:    {len(store.all_edges())}")

# Compact
checkpoint_hash = store.compact()

print(f"\nAfter compaction:")
print(f"  Oplog entries: {store.len()}")
print(f"  Live nodes:    {len(store.all_nodes())}")
print(f"  Live edges:    {len(store.all_edges())}")
print(f"  Checkpoint:    {checkpoint_hash[:16]}...")

assert store.len() == 1  # single checkpoint entry
assert len(store.all_nodes()) == 100
assert len(store.all_edges()) == 99

# Can still write after compaction
store.add_node("new", "entity", "Post-compaction", {"status": "fresh"})
assert store.len() == 2  # checkpoint + new entry
assert store.get_node("new") is not None

print(f"\n✓ Compaction works — {249} entries compressed to 1, all data preserved")
