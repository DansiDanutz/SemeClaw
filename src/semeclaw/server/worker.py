"""Base worker classes for SemeClaw server."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from semeclaw.core.eventbus import EventBus
    from semeclaw.core.events import Event


class Worker(ABC):
    """Abstract base class for server workers."""

    def __init__(self):
        """Initialize the worker."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._running = False

    @abstractmethod
    async def run(self) -> None:
        """Run the worker.

        This method should block until the worker is cancelled.
        """
        pass

    async def start(self) -> None:
        """Start the worker in a managed way."""
        self._running = True
        try:
            await self.run()
        except asyncio.CancelledError:
            self.logger.info(f"{self.__class__.__name__} cancelled")
            raise
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._running = False


class SubscriberWorker(Worker, ABC):
    """Base worker that subscribes to events."""

    def __init__(self, eventbus: EventBus):
        """Initialize the subscriber worker.

        Args:
            eventbus: The event bus to subscribe to.
        """
        super().__init__()
        self.eventbus = eventbus

    def subscribe(
        self,
        event_class: type[Event],
        handler: Callable[[Event], any],
    ) -> None:
        """Subscribe to an event type.

        Args:
            event_class: The event class to subscribe to.
            handler: The handler function.
        """
        self.eventbus.subscribe(event_class, handler)

    async def publish(self, event: Event) -> None:
        """Publish an event.

        Args:
            event: The event to publish.
        """
        await self.eventbus.publish(event)
