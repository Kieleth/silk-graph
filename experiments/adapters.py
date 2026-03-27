"""CRDT system adapters for comparative benchmarks.

Each adapter wraps one CRDT system behind a common interface.
Systems use their natural API — no artificial handicaps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SyncResult:
    """What a sync operation produces."""
    bytes_sent: int
    entries_merged: int  # 0 if system doesn't report this


class CRDTAdapter(ABC):
    """Benchmark interface for one CRDT system."""
    name: str
    version: str

    @abstractmethod
    def create_store(self, instance_id: str) -> Any:
        """Create a fresh empty store/document."""

    @abstractmethod
    def add_entity(self, store: Any, entity_id: str, props: dict) -> None:
        """Create one entity with properties."""

    @abstractmethod
    def update_field(self, store: Any, entity_id: str, key: str, value: Any) -> None:
        """Update a single field on an existing entity."""

    @abstractmethod
    def read_field(self, store: Any, entity_id: str, key: str) -> Any:
        """Read a field value."""

    @abstractmethod
    def sync_one_way(self, store_a: Any, store_b: Any) -> SyncResult:
        """Sync A → B (one direction). Returns bytes transferred."""

    @abstractmethod
    def snapshot_size(self, store: Any) -> int:
        """Full snapshot size in bytes."""

    @abstractmethod
    def fork(self, store: Any, new_id: str) -> Any:
        """Create independent copy of store."""

    def add_relationship(self, store: Any, rel_id: str, rel_type: str,
                         source_id: str, target_id: str, props: dict | None = None) -> None:
        """Create a relationship between two entities.
        Default: store as a nested reference (document CRDTs).
        Override for graph-native systems."""
        # Default: add target_id to a list on the source entity
        self.update_field(store, source_id, f"_{rel_type}", target_id)

    def read_relationships(self, store: Any, entity_id: str, rel_type: str) -> list[str]:
        """Read relationship targets. Returns list of target entity IDs."""
        val = self.read_field(store, entity_id, f"_{rel_type}")
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return [val]


# ---------------------------------------------------------------------------
# Silk adapter
# ---------------------------------------------------------------------------

class SilkAdapter(CRDTAdapter):
    name = "silk"

    def __init__(self):
        import silk
        self.version = getattr(silk, "__version__", "0.1.x")
        self._GraphStore = silk.GraphStore
        self._ontology = {
            "node_types": {
                "entity": {"properties": {}},
                "user": {"properties": {}},
                "project": {"properties": {}},
            },
            "edge_types": {
                "ASSIGNED_TO": {
                    "source_types": ["user", "entity"],
                    "target_types": ["project", "entity"],
                },
                "DEPENDS_ON": {
                    "source_types": ["project", "entity"],
                    "target_types": ["project", "entity"],
                },
            },
        }

    def create_store(self, instance_id):
        return self._GraphStore(instance_id, self._ontology)

    def add_entity(self, store, entity_id, props, entity_type="entity"):
        store.add_node(entity_id, entity_type, entity_id, props)

    def add_relationship(self, store, rel_id, rel_type, source_id, target_id, props=None):
        store.add_edge(rel_id, rel_type, source_id, target_id, props or {})

    def read_relationships(self, store, entity_id, rel_type):
        return [e["target_id"] for e in store.all_edges()
                if e["edge_type"] == rel_type and e["source_id"] == entity_id]

    def update_field(self, store, entity_id, key, value):
        store.update_property(entity_id, key, value)

    def read_field(self, store, entity_id, key):
        node = store.get_node(entity_id)
        return node["properties"].get(key) if node else None

    def sync_one_way(self, store_a, store_b):
        offer = store_b.generate_sync_offer()
        payload = store_a.receive_sync_offer(offer)
        merged = store_b.merge_sync_payload(payload)
        return SyncResult(bytes_sent=len(payload), entries_merged=merged)

    def snapshot_size(self, store):
        return len(store.snapshot())

    def fork(self, store, new_id):
        return self._GraphStore.from_snapshot(new_id, store.snapshot())


# ---------------------------------------------------------------------------
# Loro adapter
# ---------------------------------------------------------------------------

class LoroAdapter(CRDTAdapter):
    name = "loro"

    def __init__(self):
        import loro
        self.version = getattr(loro, "__version__", "1.x")
        self._loro = loro

    def create_store(self, instance_id):
        return self._loro.LoroDoc()

    def add_entity(self, store, entity_id, props):
        m = store.get_map(entity_id)
        for k, v in props.items():
            m.insert(k, v)
        store.commit()

    def update_field(self, store, entity_id, key, value):
        m = store.get_map(entity_id)
        m.insert(key, value)
        store.commit()

    def read_field(self, store, entity_id, key):
        m = store.get_map(entity_id)
        v = m.get(key)
        if v is None:
            return None
        return v.value if hasattr(v, "value") else v

    def sync_one_way(self, store_a, store_b):
        vv = store_b.oplog_vv
        update = store_a.export(self._loro.ExportMode.Updates(vv))
        store_b.import_(update)
        return SyncResult(bytes_sent=len(update), entries_merged=0)

    def snapshot_size(self, store):
        return len(store.export(self._loro.ExportMode.Snapshot()))

    def fork(self, store, new_id):
        snap = store.export(self._loro.ExportMode.Snapshot())
        doc = self._loro.LoroDoc()
        doc.import_(snap)
        return doc


# ---------------------------------------------------------------------------
# pycrdt (Yjs/Yrs) adapter
# ---------------------------------------------------------------------------

class PycrdtAdapter(CRDTAdapter):
    name = "pycrdt"

    def __init__(self):
        import pycrdt
        self.version = getattr(pycrdt, "__version__", "0.x")
        self._pycrdt = pycrdt

    def create_store(self, instance_id):
        return self._pycrdt.Doc()

    def add_entity(self, store, entity_id, props):
        store[entity_id] = self._pycrdt.Map(props)

    def update_field(self, store, entity_id, key, value):
        store[entity_id][key] = value

    def read_field(self, store, entity_id, key):
        try:
            return store[entity_id][key]
        except (KeyError, TypeError):
            return None

    def sync_one_way(self, store_a, store_b):
        # Pre-declare any maps A has that B doesn't
        b_keys = set(store_b.keys())
        for key in store_a.keys():
            if key not in b_keys:
                store_b[key] = self._pycrdt.Map()
        state_b = store_b.get_state()
        update = store_a.get_update(state_b)
        store_b.apply_update(update)
        return SyncResult(bytes_sent=len(update), entries_merged=0)

    def snapshot_size(self, store):
        return len(store.get_update())

    def fork(self, store, new_id):
        doc = self._pycrdt.Doc()
        # Pre-declare maps so apply_update populates them
        for key in store.keys():
            doc[key] = self._pycrdt.Map()
        doc.apply_update(store.get_update())
        return doc


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def available_adapters() -> list[CRDTAdapter]:
    """Return adapters for all systems that are importable."""
    adapters = []

    # Silk is always available (it's the system under test)
    try:
        adapters.append(SilkAdapter())
    except ImportError:
        pass

    try:
        adapters.append(LoroAdapter())
    except ImportError:
        pass

    try:
        adapters.append(PycrdtAdapter())
    except ImportError:
        pass

    return adapters
