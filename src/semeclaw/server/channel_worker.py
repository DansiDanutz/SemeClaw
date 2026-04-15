"""Channel worker for managing platform integrations."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from semeclaw.server.worker import Worker
from semeclaw.core.events import InboundEvent

if TYPE_CHECKING:
    from semeclaw.channel.base import Channel
    from semeclaw.core.eventbus import EventBus


class ChannelWorker(Worker):
    """Worker that manages all platform channels."""

    def __init__(self, eventbus: EventBus, channels: dict[str, Channel]):
        """Initialize the channel worker.

        Args:
            eventbus: The event bus for publishing events.
            channels: Dictionary of platform_name -> Channel instances.
        """
        super().__init__()
        self.eventbus = eventbus
        self.channels = channels
        self.logger = logging.getLogger(__name__)

    async def run(self) -> None:
        """Run all channels concurrently."""
        if not self.channels:
            self.logger.warning("No channels configured")
            return

        self.logger.info(f"Starting {len(self.channels)} channel(s)")

        try:
            # Create a task for each channel
            tasks = [
                asyncio.create_task(self._run_channel(channel))
                for channel in self.channels.values()
            ]

            # Wait for all to complete (they should block indefinitely)
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self.logger.info("ChannelWorker cancelled, stopping all channels")
            # Cancel all channel tasks
            for task in tasks:
                task.cancel()
            # Wait for cancellation to complete
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _run_channel(self, channel: Channel) -> None:
        """Run a single channel.

        Args:
            channel: The channel to run.
        """
        try:
            self.logger.info(f"Starting {channel.platform_name} channel")

            # Create the message callback
            async def on_message(content: str, source) -> None:
                event = InboundEvent(
                    session_id=f"{source}_{asyncio.current_task().get_name()}",
                    source=source,
                    content=content,
                )
                await self.eventbus.publish(event)
                self.logger.debug(
                    f"Published inbound event from {channel.platform_name}: {event.session_id}"
                )

            # Run the channel with the callback
            await channel.run(on_message)
        except asyncio.CancelledError:
            self.logger.info(f"{channel.platform_name} channel cancelled")
            await channel.stop()
            raise
        except Exception as e:
            self.logger.exception(f"Error in {channel.platform_name} channel: {e}")
            raise
