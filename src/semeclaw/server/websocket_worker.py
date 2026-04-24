"""WebSocket worker for real-time connections."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from semeclaw.core.events import OutboundEvent
from semeclaw.server.worker import SubscriberWorker

if TYPE_CHECKING:
    from semeclaw.core.eventbus import EventBus


@dataclass
class WebSocketConnection:
    """Represents an active WebSocket connection."""

    user_id: str
    session_id: str
    queue: asyncio.Queue


class WebSocketWorker(SubscriberWorker):
    """Worker that manages WebSocket connections and broadcasts events."""

    def __init__(self, eventbus: EventBus):
        """Initialize the WebSocket worker.

        Args:
            eventbus: The event bus.
        """
        super().__init__(eventbus)
        self.connections: dict[str, WebSocketConnection] = {}
        self.logger = logging.getLogger(__name__)

    async def run(self) -> None:
        """Run the WebSocket worker.

        Subscribes to OutboundEvent and broadcasts to connected clients.
        """
        self.subscribe(OutboundEvent, self._broadcast)

        # Keep the worker running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    def register_connection(self, user_id: str, session_id: str, queue: asyncio.Queue) -> None:
        """Register a new WebSocket connection.

        Args:
            user_id: The user ID.
            session_id: The session ID.
            queue: The queue to send messages to.
        """
        connection = WebSocketConnection(
            user_id=user_id,
            session_id=session_id,
            queue=queue,
        )
        self.connections[session_id] = connection
        self.logger.info(f"Registered WebSocket connection: {session_id}")

    def unregister_connection(self, session_id: str) -> None:
        """Unregister a WebSocket connection.

        Args:
            session_id: The session ID.
        """
        if session_id in self.connections:
            del self.connections[session_id]
            self.logger.info(f"Unregistered WebSocket connection: {session_id}")

    async def _broadcast(self, event: OutboundEvent) -> None:
        """Broadcast an outbound event to all connected clients.

        Args:
            event: The outbound event to broadcast.
        """
        if not self.connections:
            return

        # Create the message payload
        message = {
            "type": "outbound",
            "session_id": event.session_id,
            "source": str(event.source),
            "content": event.content,
            "timestamp": event.timestamp,
            "error": event.error,
        }

        # Send to all connections (or filter by session_id/user_id as needed)
        disconnected = []

        for session_id, connection in self.connections.items():
            try:
                # Only send to matching session
                if connection.session_id == event.session_id:
                    await asyncio.wait_for(connection.queue.put(message), timeout=5.0)
                    self.logger.debug(f"Broadcasted to {session_id}")
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout sending to {session_id}, disconnecting")
                disconnected.append(session_id)
            except Exception as e:
                self.logger.exception(f"Error broadcasting to {session_id}: {e}")
                disconnected.append(session_id)

        # Clean up disconnected clients
        for session_id in disconnected:
            self.unregister_connection(session_id)

    async def get_message(self, session_id: str, timeout: float = 30.0) -> dict | None:
        """Get the next message for a connection.

        Args:
            session_id: The session ID.
            timeout: Timeout in seconds.

        Returns:
            The message dict, or None if timeout.
        """
        if session_id not in self.connections:
            return None

        try:
            message = await asyncio.wait_for(
                self.connections[session_id].queue.get(),
                timeout=timeout,
            )
            return message
        except asyncio.TimeoutError:
            return None

    def get_connection_count(self) -> int:
        """Get the number of active connections.

        Returns:
            Count of active WebSocket connections.
        """
        return len(self.connections)
