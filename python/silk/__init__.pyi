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
        ontology: str | dict[str, Any],
        path: str | None = None,
    ) -> None:
        """Create a new graph store.

        Args:
            instance_id: Unique identifier for this instance.
            ontology: JSON string or Python dict defining node types, edge types,
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
    def extend_ontology(self, extension: str | dict[str, Any]) -> str:
        """R-03: Extend the ontology with new types, properties, or subtypes.

        Only additive (monotonic) changes allowed. Returns hex hash of the entry.

        Raises:
            ValueError: If the extension violates monotonicity rules.
        """
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
    def clock_time(self) -> tuple[int, int]:
        """Current hybrid clock as (physical_ms, logical).

        R-01: physical_ms is wall-clock time in milliseconds.
        logical is a counter for events within the same millisecond.
        """
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
        """Query all live nodes of a given type, including descendants via parent_type hierarchy."""
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
    def dfs(
        self,
        start: str,
        max_depth: int | None = None,
        edge_type: str | None = None,
    ) -> list[str]:
        """DFS traversal from a start node. Returns list of node IDs."""
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
    def receive_filtered_sync_offer(
        self, offer_bytes: bytes, node_types: list[str]
    ) -> bytes:
        """Filtered sync: only entries matching node_types (+ causal ancestors).

        Reduces bandwidth for peers that only need a subset of the graph.
        Causal closure ensures the receiver can still build a valid oplog.
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
            physical_ms (int): Wall-clock time in milliseconds (R-01 HLC).
            logical (int): Counter within same millisecond (R-01 HLC).
            local (bool): True if this store wrote it, False if received via merge.
            Plus op-specific fields (node_id, node_type, subtype, edge_id, entity_id, key, value, etc.)

        Exceptions in callbacks are logged and swallowed (error isolation).
        """
        ...
    def unsubscribe(self, sub_id: int) -> None:
        """Remove a previously registered subscription by ID."""
        ...

    # -- Gossip Peer Selection (R-05) --

    def register_peer(self, peer_id: str, address: str) -> None:
        """Register a peer for gossip sync (e.g., 'tcp://10.0.0.2:7701')."""
        ...
    def unregister_peer(self, peer_id: str) -> bool:
        """Remove a peer. Returns True if it existed."""
        ...
    def list_peers(self) -> list[dict[str, Any]]:
        """List peers: [{"peer_id": str, "address": str, "last_seen_ms": int}, ...]"""
        ...
    def select_sync_targets(self) -> list[str]:
        """Select sync targets for this round. Returns ceil(ln(N)+1) peer IDs."""
        ...
    def record_sync(self, peer_id: str) -> None:
        """Record that a sync with this peer completed."""
        ...

    # -- Time-Travel (R-06) --

    def as_of(self, physical_ms: int, logical: int = 0) -> "GraphSnapshot":
        """R-06: Create a read-only snapshot of the graph at a historical time.

        Args:
            physical_ms: Wall-clock cutoff in milliseconds.
            logical: Logical clock component (default 0).

        Returns:
            A read-only GraphSnapshot with the graph state at the given time.
        """
        ...

    # -- Quarantine (R-02) --

    def get_quarantined(self) -> list[str]:
        """Get hex hashes of quarantined entries.

        Quarantined entries are in the oplog (for CRDT convergence) but
        invisible in the materialized graph (failed ontology validation).
        The quarantine set is grow-only — entries never leave.
        """
        ...

    # -- Signing (D-027) --

    def generate_signing_key(self) -> str:
        """Generate a new ed25519 keypair. Stores the private key internally.

        Returns:
            Hex-encoded public key (64 characters, 32 bytes).
        """
        ...
    def set_signing_key(self, hex_private_key: str) -> None:
        """Load an existing ed25519 private key.

        Args:
            hex_private_key: 64 hex characters (32 bytes).
        """
        ...
    def get_public_key(self) -> str | None:
        """Get the instance's public key as hex, or None if no key is set."""
        ...
    def register_trusted_author(self, author_id: str, hex_public_key: str) -> None:
        """Register a trusted author's public key for signature verification.

        Args:
            author_id: The author string used in entries.
            hex_public_key: 64 hex characters (32 bytes).
        """
        ...
    def set_require_signatures(self, enabled: bool) -> None:
        """Toggle strict mode. When enabled, unsigned entries are rejected on merge.

        Genesis entries (DefineOntology) are always accepted regardless of this setting.
        """
        ...

    # -- Epoch Compaction (R-08) --

    def create_checkpoint(self) -> bytes:
        """R-08: Create a checkpoint entry from the current graph state.

        Returns the checkpoint as bytes (for inspection), does NOT compact yet.
        The checkpoint contains synthetic ops that reconstruct the full graph.
        """
        ...
    def compact(self) -> str:
        """R-08: Compact the oplog. Creates a checkpoint of current state,
        replaces entire oplog with the checkpoint entry.
        Returns the hex hash of the checkpoint entry.

        SAFETY: Only call when ALL peers have synced to current state.
        After compaction, the oplog contains a single checkpoint entry
        that serves as the new genesis.
        """
        ...


class GraphSnapshot:
    """R-06: Read-only snapshot of the graph at a historical point in time.

    Created by `GraphStore.as_of(physical_ms, logical)`.
    Exposes query and algorithm methods but no mutations.
    """

    def cutoff_clock(self) -> tuple[int, int]:
        """The cutoff clock used to create this snapshot: (physical_ms, logical)."""
        ...
    def instance_id(self) -> str:
        """Instance identifier of the store that created this snapshot."""
        ...

    # -- Graph queries --

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID. Returns dict or None."""
        ...
    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        """Get an edge by ID. Returns dict or None."""
        ...
    def query_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        """Query all live nodes of a given type, including descendants via parent_type hierarchy."""
        ...
    def query_nodes_by_subtype(self, subtype: str) -> list[dict[str, Any]]:
        """Query all live nodes of a given subtype."""
        ...
    def query_nodes_by_property(self, key: str, value: Any) -> list[dict[str, Any]]:
        """Query nodes by a property value."""
        ...
    def all_nodes(self) -> list[dict[str, Any]]:
        """All live nodes at this point in time."""
        ...
    def all_edges(self) -> list[dict[str, Any]]:
        """All live edges at this point in time."""
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
    def dfs(
        self,
        start: str,
        max_depth: int | None = None,
        edge_type: str | None = None,
    ) -> list[str]:
        """DFS traversal from a start node. Returns list of node IDs."""
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
