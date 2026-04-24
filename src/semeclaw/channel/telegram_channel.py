"""Telegram channel implementation — professional grade."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from semeclaw.channel.base import Channel
from semeclaw.core.events import TelegramEventSource

if TYPE_CHECKING:
    from semeclaw.utils.config import Config

TELEGRAM_MAX_LENGTH = 4096
TYPING_REFRESH_SECS = 4
TYPING_TIMEOUT_SECS = 120  # auto-stop typing after 2 min if reply never comes


class TelegramChannel(Channel):
    """Telegram platform channel."""

    def __init__(self, bot_token: str, allowed_user_ids: list[str] | None = None):
        super().__init__()
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids or []
        self.logger = logging.getLogger(__name__)
        self._application = None
        # Keyed by chat_id — keeps typing alive until reply() is called
        self._typing_tasks: dict[str, asyncio.Task] = {}

    @property
    def platform_name(self) -> str:
        return "telegram"

    def _is_user_allowed(self, user_id: str) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    def _persist_chat_id(self, chat_id: str) -> None:
        """Save the latest chat_id so cron jobs can send proactive notifications."""
        try:
            from pathlib import Path

            # Save next to the config file — wherever the bot was launched from
            Path(".telegram_chat_id").write_text(chat_id)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Typing indicator helpers                                            #
    # ------------------------------------------------------------------ #

    async def _start_typing(self, chat_id: str) -> None:
        """Start continuous typing indicator for chat_id.

        Cancels any previous typing task for that chat first.
        Runs until _stop_typing() is called or TYPING_TIMEOUT_SECS elapses.
        """
        await self._stop_typing(chat_id)
        task = asyncio.create_task(_typing_loop(self._application.bot, chat_id))
        self._typing_tasks[chat_id] = task

    async def _stop_typing(self, chat_id: str) -> None:
        """Cancel and await the typing task for chat_id (if any)."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    # ------------------------------------------------------------------ #
    #  Bot lifecycle                                                       #
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        """Run the Telegram bot using low-level async API (no event loop conflict)."""
        try:
            from telegram.ext import Application

            self._application = Application.builder().token(self.bot_token).build()
            self._application.add_handler(self._create_message_handler())

            self.logger.info("Telegram bot starting polling...")
            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling(allowed_updates=["message"])
            self.logger.info("Telegram bot is now polling for messages!")

            try:
                while self._running:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                # Cancel all active typing tasks
                for task in self._typing_tasks.values():
                    task.cancel()
                self._typing_tasks.clear()

                self.logger.info("Telegram bot stopping...")
                await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()

        except ImportError:
            self.logger.error("python-telegram-bot is not installed.")
            raise

    # ------------------------------------------------------------------ #
    #  Message handler                                                     #
    # ------------------------------------------------------------------ #

    def _create_message_handler(self):
        """Create the message handler.

        Key design: typing starts HERE (when message arrives) and stops
        in reply() (when the agent finishes). The callback just fires an
        async event and returns immediately, so we cannot tie typing to it.
        """
        from telegram.ext import ContextTypes, MessageHandler, filters

        async def message_handler(update, context: ContextTypes.DEFAULT_TYPE):
            if not (update.message and update.message.text):
                return

            user_id = str(update.message.from_user.id)
            chat_id = str(update.message.chat_id)
            username = update.message.from_user.username or "unknown"

            if not self._is_user_allowed(user_id):
                self.logger.warning(f"Unauthorized user: {user_id} (@{username})")
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
                return

            self.logger.info(f"Message from {user_id} (@{username}): {update.message.text[:80]}")

            # ✅ Auto-save chat_id so cron jobs can notify Dan proactively
            self._persist_chat_id(chat_id)

            # ✅ Start typing BEFORE calling the callback.
            # It will keep refreshing every 4 s until reply() stops it.
            await self._start_typing(chat_id)

            source = TelegramEventSource(user_id=user_id, chat_id=chat_id)

            if self._callback:
                try:
                    result = self._callback(update.message.text, source)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as e:
                    self.logger.exception(f"Callback error: {e}")
                    await self._stop_typing(chat_id)
                    await _send_safe(
                        self._application.bot,
                        chat_id,
                        "⚠️ Something went wrong. Please try again.",
                    )

        return MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)

    # ------------------------------------------------------------------ #
    #  Reply                                                               #
    # ------------------------------------------------------------------ #

    async def reply(self, content: str, source: TelegramEventSource) -> None:
        """Stop typing indicator, then send reply with Markdown + auto-splitting."""
        if not isinstance(source, TelegramEventSource):
            self.logger.warning(f"Cannot reply to non-Telegram source: {source}")
            return

        if not self._application:
            self.logger.warning("Telegram application not initialized")
            return

        chat_id = source.chat_id

        # ✅ Stop typing BEFORE sending so it disappears exactly when the
        # message appears — just like every professional bot does.
        await self._stop_typing(chat_id)

        for chunk in _split_message(content):
            await _send_safe(self._application.bot, chat_id, chunk)

    # ------------------------------------------------------------------ #
    #  Factory                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config_inner(cls, config: Config) -> TelegramChannel:
        if not config.channels or not config.channels.telegram:
            raise ValueError("Telegram config not found in channels config")
        tg_config = config.channels.telegram
        return cls(
            bot_token=tg_config.bot_token,
            allowed_user_ids=tg_config.allowed_user_ids,
        )


# ------------------------------------------------------------------ #
#  Module-level helpers (easy to unit-test)                           #
# ------------------------------------------------------------------ #


async def _typing_loop(bot, chat_id: str) -> None:
    """Send ChatAction.TYPING every TYPING_REFRESH_SECS until cancelled."""
    from telegram.constants import ChatAction

    elapsed = 0
    while elapsed < TYPING_TIMEOUT_SECS:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(TYPING_REFRESH_SECS)
        elapsed += TYPING_REFRESH_SECS


async def _send_safe(bot, chat_id: str, text: str) -> None:
    """Send message as HTML (converted from Markdown), fall back to plain text."""
    html = _md_to_html(text)
    try:
        await bot.send_message(chat_id=chat_id, text=html, parse_mode="HTML")
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logging.getLogger(__name__).exception(f"Failed to send message: {e}")


def _md_to_html(text: str) -> str:
    """Convert standard Markdown to Telegram HTML format.

    Handles: **bold**, *italic*, _italic_, `code`, ```blocks```, # headers, - bullets.
    HTML-escapes raw < > & so they don't break the parser.
    """
    placeholders: dict[str, str] = {}
    idx = [0]

    def save_block(m: re.Match) -> str:
        key = f"\x00BLOCK{idx[0]}\x00"
        idx[0] += 1
        code = m.group(2).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        placeholders[key] = f"<pre><code>{code}</code></pre>"
        return key

    def save_inline(m: re.Match) -> str:
        key = f"\x00INLINE{idx[0]}\x00"
        idx[0] += 1
        code = m.group(1).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        placeholders[key] = f"<code>{code}</code>"
        return key

    # Protect code (extract before escaping)
    text = re.sub(r"```(\w*)\n?(.*?)```", save_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", save_inline, text)

    # Escape raw HTML chars in the remaining text
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Convert Markdown → HTML
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", text)
    text = re.sub(r"_([^_\n]+)_", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)

    # Restore code placeholders
    for key, val in placeholders.items():
        text = text.replace(key, val)

    return text


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split text at paragraph boundaries, then sentences, then hard-cut."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""

    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}".lstrip("\n") if current else para
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(para) <= max_len:
            current = para
            continue

        # Para itself is too long — split by sentence
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            candidate = f"{current} {sentence}".lstrip() if current else sentence
            if len(candidate) <= max_len:
                current = candidate
                continue
            if current:
                chunks.append(current)
            # Hard-split sentences that are still too long
            while len(sentence) > max_len:
                chunks.append(sentence[:max_len])
                sentence = sentence[max_len:]
            current = sentence

    if current:
        chunks.append(current)

    return chunks or [text[:max_len]]
