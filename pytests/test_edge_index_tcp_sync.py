"""Test edge index consistency through real TCP sync.

The in-process tests (test_edge_index_consistency.py) verify that Silk's
outgoing_edges() and all_edges() agree after sync. They all pass.

This file tests the same invariant but through the REAL TCP wire protocol
that Shelob uses in production: length-prefixed, HMAC-authenticated frames
over asyncio TCP sockets. This is the layer between "Silk works" and
"production is broken".

The test spawns a real TCP server, syncs stores over localhost, and verifies
edge index consistency on the receiving side.
"""

import asyncio
import json
import os
import struct
import hmac
import hashlib
import tempfile

import pytest

from silk import GraphStore


ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {
                "status": {"value_type": "string"},
            },
            "subtypes": {
                "instance": {
                    "properties": {
                        "hostname": {"value_type": "string", "required": True},
                        "host": {"value_type": "string"},
                        "port": {"value_type": "int"},
                        "priority": {"value_type": "int", "required": True},
                    },
                },
                "server": {
                    "properties": {
                        "name": {"value_type": "string", "required": True},
                        "provider": {"value_type": "string", "required": True},
                    },
                },
                "capability": {
                    "properties": {
                        "name": {"value_type": "string", "required": True},
                        "role": {"value_type": "string", "required": True},
                        "scope": {
                            "value_type": "string",
                            "required": True,
                            "constraints": {"enum": ["server", "fleet", "external"]},
                        },
                    },
                },
                "fleet": {
                    "properties": {
                        "name": {"value_type": "string"},
                    },
                },
            },
        },
    },
    "edge_types": {
        "RUNS_ON": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "MEMBER_OF": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "DEPENDS_ON": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
        "SCOPED_TO": {
            "source_types": ["entity"],
            "target_types": ["entity"],
            "properties": {},
        },
    },
})

HMAC_SIZE = 32
SECRET = "test-gossip-key-shared"


def make_store(instance_id: str, path: str | None = None) -> GraphStore:
    return GraphStore(instance_id, ONTOLOGY, path=path)


def _add_shelob_topology(store: GraphStore, hostname: str = "gamma"):
    inst_id = f"inst-{hostname}"
    server_id = f"server-{hostname}"
    fleet_id = f"fleet-{hostname}"

    store.add_node(fleet_id, "entity", fleet_id, {"name": fleet_id}, subtype="fleet")
    store.add_node(server_id, "entity", server_id, {"name": hostname, "provider": "hetzner"}, subtype="server")
    store.add_node(inst_id, "entity", inst_id, {
        "hostname": hostname, "host": "10.0.0.1", "port": 8000, "priority": 100,
    }, subtype="instance")

    store.add_edge(f"{inst_id}-RUNS_ON-{server_id}", "RUNS_ON", inst_id, server_id)
    store.add_edge(f"{inst_id}-MEMBER_OF-{fleet_id}", "MEMBER_OF", inst_id, fleet_id)

    store.add_node(f"cap-runtime-{hostname}", "entity", "K3s", {
        "name": "K3s", "role": "container_runtime", "scope": "server",
    }, subtype="capability")
    store.add_edge(f"cap-runtime-{hostname}-RUNS_ON-{server_id}", "RUNS_ON", f"cap-runtime-{hostname}", server_id)

    store.add_node("cap-registry", "entity", "Registry", {
        "name": "Registry", "role": "container_registry", "scope": "fleet",
    }, subtype="capability")
    store.add_edge(f"cap-registry-SCOPED_TO-{fleet_id}", "SCOPED_TO", "cap-registry", fleet_id)


def _assert_index_consistent(store: GraphStore, node_id: str, label: str = ""):
    from_index = store.outgoing_edges(node_id)
    from_scan = [e for e in store.all_edges() if e["source_id"] == node_id]
    index_ids = {e["edge_id"] for e in from_index}
    scan_ids = {e["edge_id"] for e in from_scan}
    assert index_ids == scan_ids, (
        f"INCONSISTENCY ({label}) for {node_id}: "
        f"outgoing_edges={index_ids}, all_edges scan={scan_ids}, "
        f"missing from index={scan_ids - index_ids}"
    )


# ── Wire protocol (mirrors shelob/fleet/protocol.py) ───────────────


def _hmac_sign(payload: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), payload, hashlib.sha256).digest()


def _frame_encode(payload: bytes) -> bytes:
    signature = _hmac_sign(payload, SECRET)
    length = HMAC_SIZE + len(payload)
    return struct.pack("!I", length) + signature + payload


def _frame_decode(data: bytes) -> bytes | None:
    if len(data) < 4 + HMAC_SIZE:
        return None
    length = struct.unpack("!I", data[:4])[0]
    if len(data) < 4 + length:
        return None
    signature = data[4:4 + HMAC_SIZE]
    payload = data[4 + HMAC_SIZE:4 + length]
    expected = _hmac_sign(payload, SECRET)
    if not hmac.compare_digest(signature, expected):
        return None
    return payload


# ── TCP sync helpers ────────────────────────────────────────────────


async def _run_tcp_server(store: GraphStore, host: str, port: int, ready: asyncio.Event):
    """Run a TCP sync server that handles one sync exchange."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Read offer frame
        length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=5)
        length = struct.unpack("!I", length_bytes)[0]
        rest = await asyncio.wait_for(reader.readexactly(length), timeout=5)
        frame = length_bytes + rest

        payload = _frame_decode(frame)
        if payload is None:
            writer.close()
            return

        # Process: receive offer, generate response payload
        response_payload = store.receive_sync_offer(payload)

        # Send response frame
        response_frame = _frame_encode(response_payload)
        writer.write(response_frame)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, host, port)
    ready.set()
    async with server:
        await server.serve_forever()


async def _tcp_sync_client(store: GraphStore, host: str, port: int) -> int:
    """Connect to TCP sync server, send offer, receive payload, merge."""
    offer = store.generate_sync_offer()
    frame = _frame_encode(offer)

    reader, writer = await asyncio.open_connection(host, port)
    writer.write(frame)
    await writer.drain()

    # Read response
    length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=5)
    length = struct.unpack("!I", length_bytes)[0]
    rest = await asyncio.wait_for(reader.readexactly(length), timeout=5)
    response_frame = length_bytes + rest

    writer.close()
    await writer.wait_closed()

    payload = _frame_decode(response_frame)
    if payload is None or len(payload) == 0:
        return 0
    return store.merge_sync_payload(payload)


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edges_consistent_after_tcp_sync():
    """The production bug scenario: A has topology, syncs to B over TCP.
    B should find edges via both all_edges() and outgoing_edges()."""
    store_a = make_store("inst-a")
    store_b = make_store("inst-b")
    _add_shelob_topology(store_a, "gamma")

    port = 17710
    ready = asyncio.Event()
    server_task = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port, ready))

    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        merged = await _tcp_sync_client(store_b, "127.0.0.1", port)
        assert merged > 0, "No entries merged"

        # THE KEY ASSERTION: outgoing_edges must match all_edges scan
        _assert_index_consistent(store_b, "inst-gamma", "TCP sync")
        assert store_b.get_node("inst-gamma") is not None
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_edges_consistent_after_tcp_sync_with_redb():
    """TCP sync into a redb-backed store, then verify consistency."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path_b = os.path.join(tmpdir, "b.redb")

        store_a = make_store("inst-a")
        store_b = make_store("inst-b", path=path_b)
        _add_shelob_topology(store_a, "gamma")

        port = 17711
        ready = asyncio.Event()
        server_task = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port, ready))

        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            merged = await _tcp_sync_client(store_b, "127.0.0.1", port)
            assert merged > 0

            _assert_index_consistent(store_b, "inst-gamma", "TCP + redb")
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_edges_consistent_after_tcp_sync_redb_reopen():
    """TCP sync into redb, close, reopen, verify consistency."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path_b = os.path.join(tmpdir, "b.redb")

        store_a = make_store("inst-a")
        store_b = make_store("inst-b", path=path_b)
        _add_shelob_topology(store_a, "gamma")

        port = 17712
        ready = asyncio.Event()
        server_task = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port, ready))

        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            await _tcp_sync_client(store_b, "127.0.0.1", port)
            _assert_index_consistent(store_b, "inst-gamma", "before reopen")
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        del store_b
        store_b2 = make_store("inst-b", path=path_b)
        _assert_index_consistent(store_b2, "inst-gamma", "after TCP + redb reopen")


@pytest.mark.asyncio
async def test_bidirectional_tcp_sync_both_topologies():
    """Both instances have topologies, bidirectional TCP sync."""
    store_a = make_store("inst-a")
    store_b = make_store("inst-b")
    _add_shelob_topology(store_a, "gamma")
    _add_shelob_topology(store_b, "delta")

    port_a = 17713
    port_b = 17714
    ready_a = asyncio.Event()
    ready_b = asyncio.Event()

    server_a = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port_a, ready_a))
    server_b = asyncio.create_task(_run_tcp_server(store_b, "127.0.0.1", port_b, ready_b))

    try:
        await asyncio.wait_for(ready_a.wait(), timeout=2)
        await asyncio.wait_for(ready_b.wait(), timeout=2)

        # B syncs from A (gets gamma)
        await _tcp_sync_client(store_b, "127.0.0.1", port_a)
        # A syncs from B (gets delta)
        await _tcp_sync_client(store_a, "127.0.0.1", port_b)

        # Both should have consistent edge indices for the remote topology
        _assert_index_consistent(store_a, "inst-delta", "A sees delta via TCP")
        _assert_index_consistent(store_b, "inst-gamma", "B sees gamma via TCP")
    finally:
        server_a.cancel()
        server_b.cancel()
        for t in (server_a, server_b):
            try:
                await t
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_multiple_tcp_sync_rounds():
    """Multiple sync rounds over TCP (like production tick loop)."""
    store_a = make_store("inst-a")
    store_b = make_store("inst-b")
    _add_shelob_topology(store_a, "gamma")

    port = 17715
    ready = asyncio.Event()
    server_task = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port, ready))

    try:
        await asyncio.wait_for(ready.wait(), timeout=2)

        # 5 rounds (production syncs every 5s)
        for _ in range(5):
            await _tcp_sync_client(store_b, "127.0.0.1", port)

        _assert_index_consistent(store_b, "inst-gamma", "after 5 TCP rounds")
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_local_seed_then_tcp_sync_no_conflict():
    """B loads its own seed, then syncs A's topology over TCP.
    Both local and remote edges should be consistent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path_b = os.path.join(tmpdir, "b.redb")

        store_a = make_store("inst-a")
        store_b = make_store("inst-b", path=path_b)

        # B has its own topology (like delta in production)
        _add_shelob_topology(store_b, "delta")
        # A has its topology (like gamma)
        _add_shelob_topology(store_a, "gamma")

        # Verify B's own edges before sync
        _assert_index_consistent(store_b, "inst-delta", "B own before sync")

        port = 17716
        ready = asyncio.Event()
        server_task = asyncio.create_task(_run_tcp_server(store_a, "127.0.0.1", port, ready))

        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            await _tcp_sync_client(store_b, "127.0.0.1", port)

            # B should see gamma's edges AND keep its own delta edges
            _assert_index_consistent(store_b, "inst-gamma", "B sees gamma after TCP")
            _assert_index_consistent(store_b, "inst-delta", "B keeps delta after TCP")
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        # Reopen and verify both still consistent
        del store_b
        store_b2 = make_store("inst-b", path=path_b)
        _assert_index_consistent(store_b2, "inst-gamma", "gamma after reopen")
        _assert_index_consistent(store_b2, "inst-delta", "delta after reopen")
