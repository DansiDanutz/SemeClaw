"""Event bus for pub/sub and event persistence."""

from __future__ import annotations

import asyncio
import logging
import json
from pathlib import Path
from typing import Callable, Any
from datetime import datetime

from semeclaw.core.events import Event, OutboundEvent, InboundEvent, DispatchResultEvent


class EventBus:
    """Asynchronous event bus with pub/sub and persistence."""

    def __init__(self, pending_dir: Path | None = None):
        """Initialize the event bus.

        Args:
            pending_dir: Directory to persist pending outbound events for recovery.
        """
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.subscribers: dict[type[Event], list[Callable[[Event], Any]]] = {}
        self.pending_dir = pending_dir
        self.logger = logging.getLogger(__name__)

        if self.pending_dir:
            self.pending_dir.mkdir(parents=True, exist_ok=True)

    def subscribe(
        self,
        event_class: type[Event],
        handler: Callable[[Event], Any],
    ) -> None:
        """Subscribe to events of a specific type.

        Args:
            event_class: The event class to subscribe to.
            handler: Async callable that processes the event.
        """
        if event_class not in self.subscribers:
            self.subscribers[event_class] = []
        self.subscribers[event_class].append(handler)
        self.logger.debug(f"Subscribed {handler.__name__} to {event_class.__name__}")

    def unsubscribe(self, handler: Callable[[Event], Any]) -> None:
        """Unsubscribe a handler from all event types.

        Args:
            handler: The handler to unsubscribe.
        """
        for subscribers in self.subscribers.values():
            if handler in subscribers:
                subscribers.remove(handler)
        self.logger.debug(f"Unsubscribed {handler.__name__}")

    async def publish(self, event: Event) -> None:
        """Publish an event to the queue.

        Args:
            event: The event to publish.
        """
        await self.queue.put(event)
        self.logger.debug(f"Published {event.__class__.__name__} event")

    async def run(self) -> None:
        """Run the event loop, dispatching events to subscribers.

        This method blocks and runs until cancelled.
        """
        self.logger.info("EventBus starting")
        try:
            while True:
                event = await self.queue.get()
                await self._dispatch(event)
        except asyncio.CancelledError:
            self.logger.info("EventBus shutting down")
            raise

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all matching subscribers.

        Args:
            event: The event to dispatch.
        """
        # Persist outbound events before dispatching
        if isinstance(event, OutboundEvent):
            self._persist_outbound(event)

        # Get handlers for this event class and all parent classes
        handlers = []
        for event_class in type(event).__mro__:
            if event_class in self.subscribers:
                handlers.extend(self.subscribers[event_class])

        if not handlers:
            self.logger.warning(
                f"No handlers for {event.__class__.__name__} "
                f"(session={event.session_id})"
            )
            return

        for handler in handlers:
            try:
                result = handler(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                self.logger.exception(
                    f"Error in handler {handler.__name__} for {event.__class__.__name__}"
                )

    def _persist_outbound(self, event: OutboundEvent) -> None:
        """Persistently store an outbound event for recovery.

        Args:
            event: The outbound event to persist.
        """
        if not self.pending_dir:
            return

        try:
            event_id = f"{event.session_id}_{event.timestamp}"
            pending_file = self.pending_dir / f"{event_id}.json"

            data = {
                "session_id": event.session_id,
                "source": str(event.source),
                "content": event.content,
                "timestamp": event.timestamp,
                "error": event.error,
            }

            with open(pending_file, "w") as f:
                json.dump(data, f)

            self.logger.debug(f"Persisted outbound event: {pending_file}")
        except Exception as e:
            self.logger.exception(f"Failed to persist outbound event: {e}")

    def _recover(self) -> list[OutboundEvent]:
        """Recover persisted outbound events from disk.

        Returns:
            List of recovered OutboundEvent objects.
        """
        events = []
        if not self.pending_dir or not self.pending_dir.exists():
            return events

        try:
            for pending_file in self.pending_dir.glob("*.json"):
                try:
                    with open(pending_file) as f:
                        data = json.load(f)

                    from semeclaw.core.events import EventSource

                    source = EventSource.from_string(data["source"])
                    event = OutboundEvent(
                        session_id=data["session_id"],
                        source=source,
                        content=data["content"],
                        timestamp=data["timestamp"],
                        error=data.get("error"),
                    )
                    events.append(event)
                    self.logger.debug(f"Recovered event: {pending_file.name}")
                except Exception as e:
                    self.logger.exception(f"Failed to recover event {pending_file}: {e}")
        except Exception as e:
            self.logger.exception(f"Failed to scan pending directory: {e}")

        return events

    async def ack(self, event: OutboundEvent) -> None:
        """Acknowledge and remove a persisted event.

        Args:
            event: The event that was successfully delivered.
        """
        if not self.pending_dir:
            return

        try:
            event_id = f"{event.session_id}_{event.timestamp}"
            pending_file = self.pending_dir / f"{event_id}.json"

            if pending_file.exists():
                pending_file.unlink()
                self.logger.debug(f"Acknowledged event: {pending_file.name}")
        except Exception as e:
            self.logger.exception(f"Failed to acknowledge event: {e}")
