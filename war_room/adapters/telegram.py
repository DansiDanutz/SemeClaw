"""
Telegram adapter — bidirectional sync with Telegram Bot API.

The adapter wraps the Telegram Bot API to:
  - Send messages and dashboard links to users
  - Receive commands (/run, /status, /board, /link, /help)
  - Trigger War Room pipelines from Telegram chats

Bot API base: https://api.telegram.org/bot<token>
Config: TELEGRAM_BOT_TOKEN (required)

Commands:
  /run <task>     — trigger a War Room pipeline
  /status         — show War Room metrics
  /board          — show board summary
  /link           — send dashboard + meeting room links
  /help           — list available commands
  <any text>      — treated as /run <text>

When the bot token is not configured, the adapter falls back to
local JSON state for queued outgoing messages.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.telegram")

DEFAULT_BASE = "https://api.telegram.org"
TELEGRAM_STATE_DIR = Path.home() / ".semeclaw"


class TelegramAdapter(Adapter):
    """Telegram Bot adapter for War Room integration."""

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        api_base = (base_url or DEFAULT_BASE).rstrip("/")
        resolved_base = f"{api_base}/bot{resolved_token}" if resolved_token else api_base
        super().__init__("telegram", resolved_base)
        self.token = resolved_token
        self.api_base = api_base
        self._client: httpx.AsyncClient | None = None
        self._state_file = TELEGRAM_STATE_DIR / "telegram_sync.json"
        self._outbox: list[dict] = []
        self._recent_commands: list[dict] = []
        self._load_local_state()

    def _load_local_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._outbox = data.get("outbox", [])
                self._recent_commands = data.get("recent_commands", [])
            except Exception:
                self._outbox = []
                self._recent_commands = []
        else:
            self._outbox = []
            self._recent_commands = []

    def _save_local_state(self) -> None:
        try:
            TELEGRAM_STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {"outbox": self._outbox, "recent_commands": self._recent_commands},
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save Telegram local state: %s", e)

    async def _ensure_client(self) -> httpx.AsyncClient | None:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def _has_token(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        if not self._has_token():
            return AdapterHealth(
                name="telegram",
                reachable=False,
                error="TELEGRAM_BOT_TOKEN not set",
                meta={"local_state": str(self._state_file)} if self._outbox else None,
            )
        client = await self._ensure_client()
        if client is None:
            return AdapterHealth(name="telegram", reachable=False, error="no client")
        try:
            r = await client.get("/getMe")
            r.raise_for_status()
            data = r.json()
            bot = data.get("result", {})
            return AdapterHealth(
                name="telegram",
                reachable=True,
                version=bot.get("username"),
                meta={
                    "bot_name": bot.get("first_name"),
                    "bot_id": bot.get("id"),
                },
            )
        except Exception as e:
            return AdapterHealth(name="telegram", reachable=False, error=str(e))

    # ------------------------------------------------------------------ #
    #  Bot API helpers
    # ------------------------------------------------------------------ #

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        disable_web_page_preview: bool = False,
    ) -> dict | None:
        """Send a text message to a chat."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        if not self._has_token():
            self._outbox.append({"type": "message", "payload": payload, "queued_at": datetime.now(timezone.utc).isoformat()})
            self._save_local_state()
            logger.info("[LOCAL] Queued Telegram message for chat %s", chat_id)
            return {"queued": True}

        client = await self._ensure_client()
        if client is None:
            return None

        try:
            r = await client.post("/sendMessage", json=payload)
            r.raise_for_status()
            return r.json().get("result")
        except Exception as e:
            logger.error("Telegram send_message failed: %s", e)
            return None

    async def send_link(
        self,
        chat_id: int | str,
        url: str,
        text: str = "Open Dashboard",
        button_text: str = "🚀 Open War Room",
    ) -> dict | None:
        """Send a message with an inline button linking to a URL."""
        reply_markup = {
            "inline_keyboard": [[{"text": button_text, "url": url}]]
        }
        return await self.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    async def get_updates(self, offset: int | None = None, limit: int = 100) -> list[dict]:
        """Poll for new updates (messages)."""
        if not self._has_token():
            return []
        client = await self._ensure_client()
        if client is None:
            return []
        try:
            payload: dict[str, Any] = {"limit": limit, "timeout": 30}
            if offset is not None:
                payload["offset"] = offset
            r = await client.post("/getUpdates", json=payload)
            r.raise_for_status()
            return r.json().get("result", [])
        except Exception as e:
            logger.warning("Telegram getUpdates failed: %s", e)
            return []

    async def set_webhook(self, url: str, secret_token: str | None = None) -> bool:
        """Set a webhook URL for the bot."""
        if not self._has_token():
            return False
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            payload: dict[str, Any] = {"url": url}
            if secret_token:
                payload["secret_token"] = secret_token
            r = await client.post("/setWebhook", json=payload)
            r.raise_for_status()
            return r.json().get("ok", False)
        except Exception as e:
            logger.error("Telegram set_webhook failed: %s", e)
            return False

    async def delete_webhook(self) -> bool:
        """Delete the bot webhook."""
        if not self._has_token():
            return False
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            r = await client.post("/deleteWebhook")
            r.raise_for_status()
            return r.json().get("ok", False)
        except Exception as e:
            logger.error("Telegram delete_webhook failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Command processing
    # ------------------------------------------------------------------ #

    def process_command(self, text: str) -> tuple[str, str]:
        """Parse command text. Returns (command, args)."""
        text = text.strip()
        if text.startswith("/"):
            parts = text.split(None, 1)
            cmd = parts[0].split("@")[0].lower()  # strip bot username
            args = parts[1] if len(parts) > 1 else ""
            return cmd, args
        return "", text

    async def handle_update(self, update: dict[str, Any]) -> dict | None:
        """Process a single Telegram update. Returns a response dict."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return None

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "")
        if not text:
            return None

        cmd, args = self.process_command(text)

        self._recent_commands.append({
            "chat_id": chat_id,
            "command": cmd or "text",
            "args": args,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._recent_commands = self._recent_commands[-100:]  # keep last 100
        self._save_local_state()

        return {"chat_id": chat_id, "command": cmd, "args": args, "update_id": update.get("update_id")}

    async def send_help(self, chat_id: int | str) -> dict | None:
        help_text = (
            "🏛 <b>War Room Bot</b>\n\n"
            "<b>Commands:</b>\n"
            "• /run &lt;task&gt; — start a pipeline\n"
            "• /status — show metrics\n"
            "• /board — show board state\n"
            "• /link — get dashboard links\n"
            "• /help — show this message\n\n"
            "Any plain text is treated as a /run command."
        )
        return await self.send_message(chat_id, help_text)

    async def send_status(self, chat_id: int | str, state: dict[str, Any]) -> dict | None:
        metrics = state.get("metrics", {})
        text = (
            "🏛 <b>War Room Status</b>\n\n"
            f"Tasks run: {metrics.get('tasks_run', 0)}\n"
            f"Succeeded: {metrics.get('tasks_succeeded', 0)}\n"
            f"Failed: {metrics.get('tasks_failed', 0)}\n"
            f"Paperclip issues: {metrics.get('paperclip_issues_created', 0)}\n"
            f"Multica issues: {metrics.get('multica_issues_created', 0)}\n"
            f"Last updated: {state.get('last_updated') or 'never'}"
        )
        return await self.send_message(chat_id, text)

    async def send_dashboard_links(
        self,
        chat_id: int | str,
        public_url: str = "http://127.0.0.1:8765",
    ) -> dict | None:
        text = (
            "🏛 <b>War Room Links</b>\n\n"
            f"Dashboard: {public_url}\n"
            f"Meeting Room: {public_url}/agents\n"
            f"Embedded: {public_url}/embed"
        )
        await self.send_message(chat_id, text)
        return await self.send_link(
            chat_id=chat_id,
            url=f"{public_url}/agents",
            text="Click below to open your War Room meeting room 👇",
            button_text="🚀 Enter Meeting Room",
        )

    # ------------------------------------------------------------------ #
    #  Adapter interface
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        agents = await self.list_agents()
        tasks = await self.list_tasks()
        return [{"type": "agent", "data": a} for a in agents] + [{"type": "task", "data": t} for t in tasks]

    async def list_agents(self) -> list[dict[str, Any]]:
        h = await self.health()
        return [
            {
                "id": "telegram_bot",
                "name": h.version or "War Room Bot",
                "role": "Messenger",
                "status": "active" if h.reachable else "offline",
                "platform": self.name,
            }
        ]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": f"tg-{i:04d}",
                "title": cmd.get("args", "")[:60] or cmd.get("command", ""),
                "status": "done",
                "assignee": str(cmd.get("chat_id", "")),
                "platform": self.name,
                "timestamp": cmd.get("timestamp"),
            }
            for i, cmd in enumerate(self._recent_commands)
        ]

    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        ok = True
        for change in changes:
            ctype = change.get("type")
            if ctype == "send_message":
                result = await self.send_message(
                    chat_id=change.get("chat_id", ""),
                    text=change.get("text", ""),
                    parse_mode=change.get("parse_mode", "HTML"),
                )
                ok = ok and (result is not None)
            elif ctype == "send_link":
                result = await self.send_link(
                    chat_id=change.get("chat_id", ""),
                    url=change.get("url", ""),
                    text=change.get("text", "Open Dashboard"),
                )
                ok = ok and (result is not None)
        return ok
