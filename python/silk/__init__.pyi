"""Type stubs for silk."""

from typing import Any, Callable

class GraphStore:
    """Ontology-first Merkle-DAG graph store.

    Requires an ontology JSON string at creation. The ontology is validated
    for internal consistency and stored as the immutable genesis entry.
    All subsequent operations are validated against the ontology.

    Supports two modes:
    - In-memory (default): data lives only in memory.
    - Persistent: backed by redb on disk (pass `path` to constructor).
    """

    def __init__(
        self,
        instance_id: str,
        ontology_json: str,
        path: str | None = None,
    ) -> None:
        """Create a new graph store.

        Args:
            instance_id: Unique identifier for this instance.
            ontology_json: JSON string defining node types, edge types,
                           and their properties/constraints.
            path: Optional file path for persistent storage (redb).
                  If omitted, the store is purely in-memory.

        Raises:
            ValueError: If the ontology JSON is invalid or internally inconsistent.
            IOError: If the persistent store cannot be created.
        """
        ...
    @staticmethod
    def open(path: str) -> "GraphStore":
        """Open an existing persistent store (no genesis needed)."""
        ...
    @staticmethod
    def from_snapshot(instance_id: str, snapshot_bytes: bytes) -> "GraphStore":
        """Create a new in-memory store from a snapshot (bytes).

        Deserializes the snapshot, rebuilds the op log and materializes the graph.

        Args:
            instance_id: Unique identifier for this new instance.
            snapshot_bytes: MessagePack bytes from another store's snapshot() call.
        """
        ...

    # -- Mutations --

    def add_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        properties: dict[str, Any] | None = None,
        subtype: str | None = None,
    ) -> str:
        """Add a node. Returns hex hash.

        When the node type defines subtypes in the ontology, the subtype
        parameter is required and properties are validated per-subtype.
        When the node type has no subtypes, subtype must be None.
        """
        ...
    def add_edge(
        self,
        edge_id: str,
        edge_type: str,
        source_id: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Add an edge. Returns hex hash."""
        ...
    def update_property(self, entity_id: str, key: str, value: Any) -> str:
        """Update a property. Returns hex hash."""
        ...
    def remove_node(self, node_id: str) -> str:
        """Remove a node. Returns hex hash."""
        ...
    def remove_edge(self, edge_id: str) -> str:
        """Remove an edge. Returns hex hash."""
        ...

    # -- DAG introspection --

    def get(self, hex_hash: str) -> dict[str, Any] | None:
        """Get an entry by hex hash. Returns None if not found."""
        ...
    def heads(self) -> list[str]:
        """Return current DAG head hashes as hex strings."""
        ...
    def len(self) -> int:
        """Total number of entries (including genesis)."""
        ...
    def instance_id(self) -> str:
        """Instance identifier."""
        ...
    def clock_time(self) -> int:
        """Current Lamport time."""
        ...
    def ontology_json(self) -> str:
        """Return the ontology as a JSON string."""
        ...
    def node_type_names(self) -> list[str]:
        """Return the list of valid node types."""
        ...
    def edge_type_names(self) -> list[str]:
        """Return the list of valid edge types."""
        ...
    def entries_since(self, hex_hash: str | None = None) -> list[dict[str, Any]]:
        """Return all entries since a given hash (delta for sync)."""
        ...

    # -- Graph queries --

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID. Returns dict or None.

        Returns:
            {"node_id": str, "node_type": str, "subtype": str | None,
             "label": str, "properties": dict[str, Any]}
        """
        ...
    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        """Get an edge by ID. Returns dict or None.

        Returns:
            {"edge_id": str, "edge_type": str, "source_id": str,
             "target_id": str, "properties": dict[str, Any]}
        """
        ...
    def query_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        """Query all live nodes of a given type."""
        ...
    def query_nodes_by_subtype(self, subtype: str) -> list[dict[str, Any]]:
        """Query all live nodes of a given subtype."""
        ...
    def query_nodes_by_property(self, key: str, value: Any) -> list[dict[str, Any]]:
        """Query nodes by a property value."""
        ...
    def all_nodes(self) -> list[dict[str, Any]]:
        """All live nodes."""
        ...
    def all_edges(self) -> list[dict[str, Any]]:
        """All live edges."""
        ...
    def outgoing_edges(self, node_id: str) -> list[dict[str, Any]]:
        """Outgoing edges from a node."""
        ...
    def incoming_edges(self, node_id: str) -> list[dict[str, Any]]:
        """Incoming edges to a node."""
        ...
    def neighbors(self, node_id: str) -> list[str]:
        """Neighbor node IDs (via outgoing edges)."""
        ...

    # -- Engine (graph algorithms) --

    def bfs(
        self,
        start: str,
        max_depth: int | None = None,
        edge_type: str | None = None,
    ) -> list[str]:
        """BFS traversal from a start node. Returns list of node IDs."""
        ...
    def shortest_path(self, start: str, end: str) -> list[str] | None:
        """Shortest path between two nodes. Returns list of node IDs or None."""
        ...
    def impact_analysis(self, node_id: str, max_depth: int | None = None) -> list[str]:
        """Impact analysis: what depends on this node? Returns list of node IDs."""
        ...
    def subgraph(self, start: str, hops: int) -> dict[str, list[str]]:
        """Subgraph extraction: nodes and edges within N hops."""
        ...
    def pattern_match(self, type_sequence: list[str]) -> list[list[str]]:
        """Find chains matching a sequence of node types."""
        ...
    def topological_sort(self) -> list[str] | None:
        """Topological sort. Returns list of node IDs or None if cycle detected."""
        ...
    def has_cycle(self) -> bool:
        """Cycle detection. Returns true if graph has a cycle."""
        ...

    # -- Sync protocol --

    def generate_sync_offer(self) -> bytes:
        """Generate a sync offer (heads + bloom filter) as bytes.

        Send this to a peer so they can compute which entries you're missing.
        """
        ...
    def receive_sync_offer(self, offer_bytes: bytes) -> bytes:
        """Receive a remote peer's sync offer and compute the payload to send back.

        Takes the remote offer as bytes, returns a sync payload
        (entries + need list) as bytes.
        """
        ...
    def merge_sync_payload(self, payload_bytes: bytes) -> int:
        """Merge a sync payload (entries received from a peer) into this store.

        Returns the number of new entries merged. Updates the materialized graph.
        """
        ...
    def merge_entries_bytes(self, entries_bytes: bytes) -> int:
        """Merge raw entries (as bytes) into this store. Returns count merged."""
        ...
    def snapshot(self) -> bytes:
        """Generate a full snapshot (all entries) as bytes.

        Used to bootstrap new peers. The new peer calls `from_snapshot` to
        create a store from this data.
        """
        ...

    # -- Subscriptions (D-023) --

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> int:
        """Register a callback for graph change notifications.

        The callback is invoked synchronously after each entry is applied,
        for both local writes and remote merges. Multiple subscribers are
        supported. Returns a subscription ID for unsubscribing.

        The callback receives a dict with fields:
            hash (str): Content-addressed entry hash (hex).
            op (str): "add_node" | "add_edge" | "update_property"
                      | "remove_node" | "remove_edge" | "define_ontology".
            author (str): Instance ID of the writer.
            clock_time (int): Lamport time.
            local (bool): True if this store wrote it, False if received via merge.
            Plus op-specific fields (node_id, node_type, subtype, edge_id, entity_id, key, value, etc.)

        Exceptions in callbacks are logged and swallowed (error isolation).
        """
        ...
    def unsubscribe(self, sub_id: int) -> None:
        """Remove a previously registered subscription by ID."""
        ...


class ObservationLog:
    """Append-only, TTL-pruned observation store (D-025, SA-014).

    The 'log' half of Silk's log/KG duality. Stores raw observations
    (health checks, metrics, container status). Local-only, never syncs.
    Backed by a separate redb file from GraphStore.
    """

    def __new__(cls, path: str, max_age_secs: int = 86400) -> "ObservationLog":
        """Open or create an observation log at the given path."""
        ...
    def append(self, source: str, value: float, metadata: dict[str, str] | None = None) -> None:
        """Append a single observation."""
        ...
    def query(self, source: str, since_ts_ms: int) -> list[dict[str, Any]]:
        """Query observations for a source since a timestamp (ms)."""
        ...
    def query_latest(self, source: str) -> dict[str, Any] | None:
        """Get the most recent observation for a source."""
        ...
    def sources(self) -> list[str]:
        """List distinct source names that have observations."""
        ...
    def truncate(self, before_ts_ms: int) -> int:
        """Delete observations older than before_ts_ms. Returns count deleted."""
        ...
    def count(self) -> int:
        """Total number of observations in the log."""
        ...
