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

    def __init__(self, bot_token: str, allowed_user_ids: list[str] | None = None):
        """Initialize the Telegram channel.

        Args:
            bot_token: The Telegram bot API token.
            allowed_user_ids: List of allowed Telegram user IDs. Empty = allow all.
        """
        super().__init__()
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids or []
        self.logger = logging.getLogger(__name__)
        self._application = None

    @property
    def platform_name(self) -> str:
        """Get platform name."""
        return "telegram"

    def _is_user_allowed(self, user_id: str) -> bool:
        """Check if a user ID is allowed."""
        if not self.allowed_user_ids:
            return True  # No restrictions if list is empty
        return user_id in self.allowed_user_ids

    async def _run(self) -> None:
        """Run the Telegram bot using low-level async API (no event loop conflict)."""
        import asyncio

        try:
            from telegram.ext import Application

            self._application = Application.builder().token(self.bot_token).build()

            # Register message handler
            self._application.add_handler(
                self._create_message_handler(),
            )

            self.logger.info("Telegram bot starting polling...")

            # Use low-level async methods instead of run_polling()
            # which tries to manage its own event loop and conflicts
            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling(allowed_updates=["message"])

            self.logger.info("Telegram bot is now polling for messages!")

            # Keep running until stopped
            try:
                while self._running:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                self.logger.info("Telegram bot stopping...")
                await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()

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

                # Check if user is allowed
                if not self._is_user_allowed(user_id):
                    self.logger.warning(
                        f"Unauthorized Telegram user: {user_id} "
                        f"(@{update.message.from_user.username})"
                    )
                    await update.message.reply_text(
                        "Sorry, you are not authorized to use this bot."
                    )
                    return

                self.logger.info(
                    f"Message from Telegram user {user_id} "
                    f"(@{update.message.from_user.username}): "
                    f"{update.message.text[:50]}..."
                )

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
            config: Configuration object with channels.telegram config.

        Returns:
            Configured TelegramChannel instance.
        """
        if not config.channels or not config.channels.telegram:
            raise ValueError("Telegram config not found in channels config")

        tg_config = config.channels.telegram
        return cls(
            bot_token=tg_config.bot_token,
            allowed_user_ids=tg_config.allowed_user_ids,
        )
