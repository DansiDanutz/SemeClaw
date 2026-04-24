"""WebSocket connection manager for the War Room dashboard."""

from __future__ import annotations

import json
from typing import Set

try:
    from fastapi import WebSocket
except ImportError:  # pragma: no cover
    WebSocket = object  # type: ignore[misc,assignment]


class ConnectionManager:
    """Manages WebSocket connections for live dashboard updates."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        """Send a message to all connected clients.

        Iterates over a snapshot of ``active_connections`` because each
        ``await ws.send_text(...)`` suspends this coroutine, during which
        another task may call :meth:`disconnect` and mutate the underlying
        set — that would raise ``RuntimeError: Set changed size during
        iteration``.
        """
        data = json.dumps(message)
        dead: set = set()
        for ws in list(self.active_connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
