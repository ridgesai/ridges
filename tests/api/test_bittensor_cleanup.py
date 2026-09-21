import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from async_substrate_interface.async_substrate import AsyncSubstrateInterface, Websocket
from async_substrate_interface.errors import SubstrateRequestException
from async_substrate_interface.types import RuntimeCache
from bittensor.core.async_subtensor import AsyncSubtensor
from websockets.asyncio.client import ClientConnection
from websockets.client import ClientProtocol
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.protocol import State
from websockets.server import ServerProtocol
from websockets.uri import parse_uri

import utils.bittensor as ridges_bittensor
from utils.bittensor import SubtensorClient


class _PeerTransport(asyncio.Transport):
    """In-memory peer for real WebSocket close methods, with optional silence."""

    def __init__(self, connection):
        self.connection = connection
        self.peer = ServerProtocol(state=State.OPEN)
        self.silent = False
        self.closed = False
        self.abort_calls = 0
        self.written = asyncio.Event()

    def set_write_buffer_limits(self, high=None, low=None):
        pass

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass

    def write(self, data):
        self.written.set()
        if self.silent:
            return
        self.peer.receive_data(data)
        loop = asyncio.get_running_loop()
        for response in self.peer.data_to_send():
            if response:
                loop.call_soon(self.connection.data_received, response)
            else:
                loop.call_soon(self.close)

    def is_closing(self):
        return self.closed

    def can_write_eof(self):
        return False

    def close(self):
        if not self.closed:
            self.closed = True
            self.connection.connection_lost(None)

    def abort(self):
        self.abort_calls += 1
        self.close()


@pytest.fixture
async def cleanup_connection(monkeypatch):
    # Exercise the pinned dependency close/shutdown methods without dialing a node.
    monkeypatch.setattr(ridges_bittensor, "_CLOSE_TIMEOUT_SECONDS", 0.05)
    connection = ClientConnection(ClientProtocol(parse_uri("ws://unused"), state=State.OPEN))
    connection.response = Response(101, "Switching Protocols", Headers())  # Handshake already completed.
    transport = _PeerTransport(connection)
    connection.connection_made(transport)
    manager = Websocket("ws://unused", shutdown_timer=None)
    manager.ws = connection
    handler = asyncio.create_task(asyncio.Event().wait())
    manager._send_recv_task = handler
    manager.shutdown = AsyncMock(wraps=manager.shutdown)
    substrate = AsyncSubstrateInterface.__new__(AsyncSubstrateInterface)
    substrate.ws = manager
    substrate.startup_runtime_task = None
    subtensor = AsyncSubtensor.__new__(AsyncSubtensor)
    subtensor.substrate = substrate
    fixture = SimpleNamespace(
        subtensor=subtensor, manager=manager, connection=connection, transport=transport, handler=handler
    )
    try:
        yield fixture
    finally:
        transport.close()
        tasks = [handler]
        if substrate.startup_runtime_task is not None:
            tasks.append(substrate.startup_runtime_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _assert_closed(fixture):
    assert fixture.transport.closed
    assert fixture.connection.state == State.CLOSED
    assert fixture.handler.done()
    assert fixture.manager.ws is None
    assert fixture.manager._send_recv_task is None


@pytest.mark.anyio
async def test_cleanup_healthy_connection_needs_no_fallback(cleanup_connection):
    fixture = cleanup_connection
    client = SubtensorClient()

    await client._close_client(fixture.subtensor)

    _assert_closed(fixture)
    fixture.manager.shutdown.assert_awaited_once()
    assert fixture.transport.abort_calls == 0

    await client._close_client(fixture.subtensor)
    _assert_closed(fixture)
    assert fixture.transport.abort_calls == 0


@pytest.mark.anyio
async def test_cleanup_failed_runtime_still_stops_socket_and_handler(cleanup_connection):
    fixture = cleanup_connection

    async def fail_runtime():
        raise ValueError("runtime initialization failed")

    runtime = asyncio.create_task(fail_runtime())
    await asyncio.wait([runtime])
    fixture.subtensor.substrate.startup_runtime_task = runtime

    await SubtensorClient()._close_client(fixture.subtensor)

    _assert_closed(fixture)
    fixture.manager.shutdown.assert_awaited_once()
    assert fixture.transport.abort_calls > 0


@pytest.mark.anyio
async def test_cleanup_silent_peer_aborts_after_close_timeout(cleanup_connection):
    fixture = cleanup_connection
    fixture.transport.silent = True

    await SubtensorClient()._close_client(fixture.subtensor)

    _assert_closed(fixture)
    assert fixture.manager.shutdown.await_count == 2
    assert fixture.transport.abort_calls > 0


@pytest.mark.anyio
async def test_cleanup_detects_swallowed_close_error_after_socket_reference_is_cleared(cleanup_connection):
    fixture = cleanup_connection
    fixture.connection.close = AsyncMock(side_effect=RuntimeError("socket close failed"))

    await SubtensorClient()._close_client(fixture.subtensor)

    _assert_closed(fixture)
    assert fixture.manager.shutdown.await_count == 2
    assert fixture.transport.abort_calls > 0


@pytest.mark.anyio
async def test_cleanup_preserves_caller_cancellation_and_closes_socket(cleanup_connection):
    fixture = cleanup_connection
    fixture.transport.silent = True
    cleanup = asyncio.create_task(SubtensorClient()._close_client(fixture.subtensor))
    await fixture.transport.written.wait()

    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup

    _assert_closed(fixture)


@pytest.mark.anyio
async def test_cleanup_releases_rpc_orphaned_by_failed_runtime_gather(cleanup_connection):
    fixture = cleanup_connection
    substrate = fixture.subtensor.substrate
    substrate.ss58_format = 42
    substrate.runtime_cache = RuntimeCache()
    fixture.transport.silent = True

    async def fail_parent_header(block_hash):
        while fixture.manager._waiting_for_response == 0:
            await asyncio.sleep(0)
        raise SubstrateRequestException("node rejected parent header")

    substrate.get_parent_block_hash = fail_parent_header
    before = set(asyncio.all_tasks())
    runtime = asyncio.create_task(substrate._get_runtime_for_version(1, "0xhead"))
    substrate.startup_runtime_task = runtime
    children = set()
    try:
        with pytest.raises(SubstrateRequestException, match="parent header"):
            await asyncio.wait_for(asyncio.shield(runtime), 1)
        # The real SDK gather leaves its other header RPC running after the error.
        children = set(asyncio.all_tasks()) - before - {runtime}
        assert len(children) == 1
        assert fixture.manager._waiting_for_response == 1

        await SubtensorClient()._close_client(fixture.subtensor)

        _, pending = await asyncio.wait(children, timeout=0.5)
        assert not pending
        assert fixture.manager._waiting_for_response == 0
        assert not fixture.manager._received
        assert not fixture.manager._inflight
        _assert_closed(fixture)
    finally:
        for child in children:
            child.cancel()
        await asyncio.gather(*children, return_exceptions=True)
