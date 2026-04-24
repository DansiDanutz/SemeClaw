"""WebSocket connection manager for the War Room dashboard."""

from __future__ import annotations

import json
from typing import Any

try:
    from fastapi import WebSocket
except ImportError:  # pragma: no cover
    WebSocket = object  # type: ignore[misc,assignment]


# Optional async hook invoked before each broadcast. Use cases: transcript
# persistence (meeting_log), metrics, shadow-mirroring. Hook may mutate the
# message (e.g. stamp a seq). Failures are logged and do not block the broadcast.
_BroadcastHook = Any


def _bump_gauge(delta: int) -> None:
    """Best-effort Prom gauge update — never raises."""
    try:
        from war_room.dashboard.metrics import ws_connections_active

        if delta > 0:
            ws_connections_active.inc(delta)
        else:
            ws_connections_active.dec(-delta)
    except Exception:  # noqa: BLE001
        pass


def _count_broadcast(msg_type: str) -> None:
    """Increment the per-type broadcast counter. Never raises."""
    try:
        from war_room.dashboard.metrics import meeting_broadcasts_total

        meeting_broadcasts_total.labels(type=msg_type).inc()
    except Exception:  # noqa: BLE001
        pass


class ConnectionManager:
    """Manages WebSocket connections for live dashboard updates."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self._before_broadcast: Any = None

    def set_before_broadcast_hook(self, hook: Any) -> None:
        """Register an ``async def hook(message: dict) -> None`` called
        before every broadcast. Pass ``None`` to unregister."""
        self._before_broadcast = hook

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        _bump_gauge(1)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        _bump_gauge(-1)

    async def broadcast(self, message: dict) -> None:
        """Send a message to all connected clients.

        Iterates over a snapshot of ``active_connections`` because each
        ``await ws.send_text(...)`` suspends this coroutine, during which
        another task may call :meth:`disconnect` and mutate the underlying
        set — that would raise ``RuntimeError: Set changed size during
        iteration``.
        """
        hook = self._before_broadcast
        if hook is not None:
            try:
                await hook(message)
            except Exception as exc:  # noqa: BLE001 — hook must never break broadcast
                import logging

                logging.getLogger("war_room.websocket").error(
                    "before_broadcast hook failed: %s",
                    exc,
                )
        _count_broadcast(message.get("type", "unknown"))
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
