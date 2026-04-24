"""Delivery worker for sending outbound events to platforms."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from semeclaw.core.events import EventSource, OutboundEvent
from semeclaw.server.worker import SubscriberWorker

if TYPE_CHECKING:
    from semeclaw.channel.base import Channel
    from semeclaw.core.eventbus import EventBus


class DeliveryWorker(SubscriberWorker):
    """Worker that delivers outbound events to platform channels."""

    # Message size limits per platform
    MESSAGE_LIMITS = {
        "telegram": 4096,
        "discord": 2000,
        "default": 1024,
    }

    def __init__(self, eventbus: EventBus, channels: dict[str, Channel]):
        """Initialize the delivery worker.

        Args:
            eventbus: The event bus.
            channels: Dictionary of platform_name -> Channel instances.
        """
        super().__init__(eventbus)
        self.channels = channels
        self.logger = logging.getLogger(__name__)

    async def run(self) -> None:
        """Run the delivery worker.

        Subscribes to OutboundEvent and delivers to appropriate channels.
        """
        self.subscribe(OutboundEvent, self._handle_outbound)

        # Keep the worker running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    async def _handle_outbound(self, event: OutboundEvent) -> None:
        """Handle an outbound event by delivering to the appropriate channel.

        Args:
            event: The outbound event to deliver.
        """
        try:
            # Find the appropriate channel for this source
            if not event.source.is_platform:
                self.logger.warning(f"Cannot deliver to non-platform source: {event.source}")
                return

            platform_name = event.source.platform_name
            if platform_name not in self.channels:
                self.logger.warning(f"No channel for platform: {platform_name}")
                return

            channel = self.channels[platform_name]

            # Split message if needed
            messages = self._chunk_message(event.content, platform_name)

            for message_chunk in messages:
                await self._deliver_with_retry(
                    channel,
                    message_chunk,
                    event.source,
                    max_retries=3,
                )

            # Acknowledge the event (remove from pending)
            await self.eventbus.ack(event)
            self.logger.info(f"Delivered event {event.session_id} to {platform_name}")

        except Exception as e:
            self.logger.exception(f"Error delivering outbound event: {e}")

    async def _deliver_with_retry(
        self,
        channel: Channel,
        content: str,
        source: EventSource,
        max_retries: int = 3,
    ) -> None:
        """Deliver a message with retry logic.

        Args:
            channel: The channel to deliver through.
            content: The message content.
            source: The target event source.
            max_retries: Maximum number of retry attempts.
        """
        for attempt in range(max_retries):
            try:
                await channel.reply(content, source)
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff
                    self.logger.warning(f"Delivery attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error(f"Failed to deliver after {max_retries} attempts: {e}")
                    raise

    def _chunk_message(self, content: str, platform_name: str) -> list[str]:
        """Split a message into chunks if it exceeds platform limits.

        Args:
            content: The message content.
            platform_name: The target platform.

        Returns:
            List of message chunks.
        """
        limit = self.MESSAGE_LIMITS.get(platform_name, self.MESSAGE_LIMITS["default"])

        if len(content) <= limit:
            return [content]

        # Split on word boundaries if possible
        chunks = []
        current_chunk = ""

        for word in content.split():
            if len(current_chunk) + len(word) + 1 <= limit:
                if current_chunk:
                    current_chunk += " "
                current_chunk += word
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = word

        if current_chunk:
            chunks.append(current_chunk)

        self.logger.debug(f"Split message into {len(chunks)} chunks for {platform_name} (limit={limit})")
        return chunks
