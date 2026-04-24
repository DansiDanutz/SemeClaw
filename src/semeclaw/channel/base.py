"""Base channel class for platform integrations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from semeclaw.core.events import EventSource

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


class Channel(ABC):
    """Abstract base class for platform channels."""

    def __init__(self):
        """Initialize the channel."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._running = False
        self._callback: Callable[[str, EventSource], Any] | None = None

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Get the name of the platform this channel handles.

        Returns:
            Platform name (e.g., 'telegram', 'discord').
        """
        pass

    async def run(self, on_message: Callable[[str, EventSource], Any]) -> None:
        """Run the channel and listen for messages.

        Args:
            on_message: Callback function that receives (message_content, event_source).
                       Should be async-capable.

        This method should block until the channel is stopped.
        """
        self._callback = on_message
        self._running = True
        try:
            await self._run()
        except Exception as e:
            self.logger.exception(f"Error in channel run: {e}")
            raise

    @abstractmethod
    async def _run(self) -> None:
        """Internal implementation of channel run loop."""
        pass

    def is_allowed(self, source: EventSource) -> bool:
        """Check if a source is allowed to use this channel.

        Args:
            source: The event source to check.

        Returns:
            True if the source can use this channel.
        """
        if not source.is_platform:
            return False
        return source.platform_name == self.platform_name

    @abstractmethod
    async def reply(self, content: str, source: EventSource) -> None:
        """Send a reply message to a source.

        Args:
            content: The message content to send.
            source: The event source to send to.
        """
        pass

    async def stop(self) -> None:
        """Stop the channel gracefully."""
        self._running = False

    @staticmethod
    def from_config(config: Config) -> dict[str, Channel]:
        """Create channel instances from configuration.

        Args:
            config: The configuration object.

        Returns:
            Dictionary of platform_name -> Channel instances.
        """
        channels = {}

        if not config.channels or not config.channels.enabled:
            return channels

        # Import specific channel implementations
        try:
            from semeclaw.channel.telegram_channel import TelegramChannel

            if config.channels.telegram:
                channels["telegram"] = TelegramChannel.from_config_inner(config)
        except ImportError:
            pass

        return channels

    @classmethod
    def from_config_inner(cls, config: Config) -> Channel:
        """Create a single channel instance from configuration.

        Args:
            config: The configuration object.

        Returns:
            A channel instance.
        """
        raise NotImplementedError(f"{cls.__name__} must implement from_config_inner if used with from_config")
