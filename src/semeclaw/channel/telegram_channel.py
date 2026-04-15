"""Telegram channel implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from semeclaw.channel.base import Channel
from semeclaw.core.events import TelegramEventSource

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


class TelegramChannel(Channel):
    """Telegram platform channel."""

    def __init__(self, api_token: str):
        """Initialize the Telegram channel.

        Args:
            api_token: The Telegram bot API token.
        """
        super().__init__()
        self.api_token = api_token
        self.logger = logging.getLogger(__name__)

        # Lazy import to avoid hard dependency
        self._bot = None
        self._application = None

    @property
    def platform_name(self) -> str:
        """Get platform name."""
        return "telegram"

    async def _run(self) -> None:
        """Run the Telegram bot."""
        try:
            from telegram.ext import Application

            self._application = Application.builder().token(self.api_token).build()

            # Register message handler
            self._application.add_handler(
                self._create_message_handler(),
            )

            await self._application.run_polling(allowed_updates=["message"])
        except ImportError:
            self.logger.error(
                "python-telegram-bot is not installed. "
                "Install with: pip install python-telegram-bot"
            )
            raise

    def _create_message_handler(self):
        """Create a Telegram message handler."""
        try:
            from telegram.ext import MessageHandler, filters, ContextTypes
        except ImportError:
            raise ImportError("python-telegram-bot is required")

        async def message_handler(update, context: ContextTypes.DEFAULT_TYPE):
            if update.message and update.message.text:
                user_id = str(update.message.from_user.id)
                chat_id = str(update.message.chat_id)

                source = TelegramEventSource(user_id=user_id, chat_id=chat_id)

                # Call the registered callback
                if self._callback:
                    result = self._callback(update.message.text, source)
                    if hasattr(result, "__await__"):
                        await result

        return MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)

    async def reply(self, content: str, source: TelegramEventSource) -> None:
        """Send a reply to Telegram.

        Args:
            content: The message content.
            source: The Telegram event source with chat_id.
        """
        if not isinstance(source, TelegramEventSource):
            self.logger.warning(f"Cannot reply to non-Telegram source: {source}")
            return

        if not self._application:
            self.logger.warning("Telegram application not initialized")
            return

        try:
            await self._application.bot.send_message(
                chat_id=source.chat_id,
                text=content,
            )
            self.logger.debug(f"Sent message to Telegram chat {source.chat_id}")
        except Exception as e:
            self.logger.exception(f"Failed to send Telegram message: {e}")

    @classmethod
    def from_config_inner(cls, config: Config) -> TelegramChannel:
        """Create from configuration.

        Args:
            config: Configuration object with telegram.api_token.

        Returns:
            Configured TelegramChannel instance.
        """
        if not hasattr(config, "telegram") or not config.telegram:
            raise ValueError("Telegram config not found")

        return cls(api_token=config.telegram["api_token"])
