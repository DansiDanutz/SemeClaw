"""Tests for the WebSocket connection manager.

Regression test for the set-mutation-during-iteration race:
``broadcast`` iterates ``active_connections`` while ``await send_text``
suspends, during which another task may call ``disconnect``. Before the
fix (iterating the set directly), concurrent churn raised
``RuntimeError: Set changed size during iteration``.
"""

from __future__ import annotations

import asyncio

import pytest

from war_room.dashboard.websocket_manager import ConnectionManager


class _FakeWebSocket:
    """Minimal stand-in for a FastAPI WebSocket used in broadcast tests."""

    def __init__(self, manager: ConnectionManager | None = None, disconnect_during_send: bool = False) -> None:
        self._manager = manager
        self._disconnect_during_send = disconnect_during_send
        self.sent: list[str] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, data: str) -> None:
        if self._disconnect_during_send and self._manager is not None:
            # Simulate another coroutine dropping a sibling connection
            # while we are mid-iteration.
            for peer in list(self._manager.active_connections):
                if peer is not self:
                    self._manager.disconnect(peer)
                    break
        # Yield control so the scheduler can interleave work.
        await asyncio.sleep(0)
        self.sent.append(data)


@pytest.mark.asyncio
async def test_broadcast_survives_concurrent_disconnect() -> None:
    mgr = ConnectionManager()
    a = _FakeWebSocket(manager=mgr, disconnect_during_send=True)
    b = _FakeWebSocket()
    c = _FakeWebSocket()
    # Attach without going through accept/add path so we don't need a real server.
    mgr.active_connections.update({a, b, c})

    # Must not raise; must deliver to at least the socket that triggered churn.
    await mgr.broadcast({"type": "test"})

    assert a.sent, "socket that triggered the churn should still have received"
    # The exact count on the others depends on iteration order, but the key
    # property is that we did not raise mid-iteration.


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections() -> None:
    class _ExplodingWebSocket(_FakeWebSocket):
        async def send_text(self, data: str) -> None:  # type: ignore[override]
            raise RuntimeError("boom")

    mgr = ConnectionManager()
    alive = _FakeWebSocket()
    dead = _ExplodingWebSocket()
    mgr.active_connections.update({alive, dead})

    await mgr.broadcast({"type": "test"})

    assert alive.sent == ['{"type": "test"}']
    assert dead not in mgr.active_connections
    assert alive in mgr.active_connections
