"""
SemeClaw Telegram Bridge — @SemeClaw_bot

Forwards Telegram messages to the War Room task-meeting endpoint and streams
agent turns back to the chat.

Env:
  SEMECLAW_BOT_TOKEN / TELEGRAM_BOT_TOKEN
                        Telegram bot token for @SemeClaw_bot
  SEMECLAW_API_BASE / SEMECLAW_API / SEMECLAW_PUBLIC_URL
                        War Room base URL (default: http://127.0.0.1:8765)
  SEMECLAW_ALLOWED_CHAT / TELEGRAM_CHAT_ID
                        Optional allowed chat IDs. Empty = open.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("semeclaw-tg")


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


BOT_TOKEN = _first_env("SEMECLAW_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
API_BASE = _first_env(
    "SEMECLAW_API_BASE",
    "SEMECLAW_API",
    "SEMECLAW_PUBLIC_URL",
    default="http://127.0.0.1:8765",
).rstrip("/")
_allowed_raw = ",".join(
    value
    for value in (
        os.environ.get("SEMECLAW_ALLOWED_CHAT", "").strip(),
        os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    )
    if value
)
ALLOWED = {c.strip() for c in _allowed_raw.split(",") if c.strip()}
API_KEY = os.environ.get("SEMECLAW_API_KEY", "").strip()

if not BOT_TOKEN:
    raise SystemExit("SEMECLAW_BOT_TOKEN / TELEGRAM_BOT_TOKEN not set")

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

WELCOME = (
    "👋 *SemeClaw War Room*\n\n"
    "Send me a task and I'll spin up a multi-agent meeting to plan it.\n\n"
    "Example: _Build a landing page for my SaaS_\n\n"
    "Commands:\n"
    "/start — this message\n"
    "/status — show active meeting\n"
    "/cancel — stop tracking current meeting"
)

# chat_id -> current meeting tracking task
_active: dict[str, asyncio.Task] = {}


def _api_headers() -> dict[str, str]:
    if not API_KEY:
        return {}
    return {"Authorization": f"Bearer {API_KEY}"}


async def tg_send(client: httpx.AsyncClient, chat_id: str, text: str, parse: str = "Markdown") -> None:
    try:
        # Telegram max message 4096 chars — chunk if needed
        for i in range(0, len(text), 3800):
            chunk = text[i : i + 3800]
            await client.post(
                f"{TG}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": parse, "disable_web_page_preview": True},
                timeout=15,
            )
    except Exception as e:
        log.warning("sendMessage failed: %s", e)


async def track_meeting(chat_id: str, meeting_id: str) -> None:
    """Poll the meeting and stream new turns to the chat until finished."""
    seen = 0
    deadline = time.time() + 20 * 60  # 20 min cap
    async with httpx.AsyncClient(timeout=15) as client:
        await tg_send(
            client,
            chat_id,
            f"🧠 Meeting `{meeting_id}` started — streaming agent turns as they arrive…",
        )
        while time.time() < deadline:
            try:
                r = await client.get(
                    f"{API_BASE}/api/meeting/history/{meeting_id}",
                    headers=_api_headers(),
                )
                if r.status_code != 200:
                    await asyncio.sleep(3)
                    continue
                session = r.json()
                turns = session.get("turns", [])
                for turn in turns[seen:]:
                    speaker = turn.get("speaker") or turn.get("agent") or "agent"
                    text = (turn.get("text") or turn.get("message") or "").strip()
                    if text:
                        await tg_send(client, chat_id, f"*{speaker}*\n{text}")
                seen = len(turns)
                if session.get("status") != "running":
                    assigns = session.get("assignments") or []
                    if assigns:
                        lines = ["✅ *Meeting complete — assignments:*"]
                        for a in assigns:
                            who = a.get("agent") or a.get("owner") or "?"
                            what = a.get("task") or a.get("description") or ""
                            lines.append(f"• *{who}* — {what}")
                        await tg_send(client, chat_id, "\n".join(lines))
                    else:
                        await tg_send(client, chat_id, "✅ Meeting complete.")
                    return
            except Exception as e:
                log.warning("poll error: %s", e)
            await asyncio.sleep(3)
        await tg_send(client, chat_id, "⏱️ Meeting tracking timed out (20 min). Meeting may still be running.")


async def handle_message(client: httpx.AsyncClient, msg: dict) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    user = (msg.get("from") or {}).get("username") or (msg.get("from") or {}).get("first_name") or "anon"

    if not chat_id or not text:
        return

    if ALLOWED and chat_id not in ALLOWED:
        await tg_send(client, chat_id, "⛔ This bot is private. Ask Dan for access.\nYour chat ID: `" + chat_id + "`")
        return

    if text.startswith("/start") or text.startswith("/help"):
        await tg_send(client, chat_id, WELCOME)
        return

    if text.startswith("/status"):
        if chat_id in _active and not _active[chat_id].done():
            await tg_send(client, chat_id, "📡 A meeting is currently being tracked for this chat.")
        else:
            await tg_send(client, chat_id, "💤 No active meeting. Send a task to start one.")
        return

    if text.startswith("/cancel"):
        t = _active.pop(chat_id, None)
        if t and not t.done():
            t.cancel()
            await tg_send(client, chat_id, "🛑 Tracking cancelled.")
        else:
            await tg_send(client, chat_id, "No active tracking.")
        return

    if text.startswith("/"):
        if text.startswith("/run"):
            parts = text.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                await tg_send(client, chat_id, "Usage: /run <task>")
                return
            text = parts[1].strip()
        else:
            await tg_send(client, chat_id, "Unknown command. Send a task or /help.")
            return

    # Cancel prior tracking for this chat
    prev = _active.pop(chat_id, None)
    if prev and not prev.done():
        prev.cancel()

    # Fire the meeting
    try:
        r = await client.post(
            f"{API_BASE}/api/meeting/task",
            json={"task": text, "user_context": f"telegram:@{user}", "lang": "en"},
            headers=_api_headers(),
            timeout=20,
        )
        if r.status_code != 200:
            await tg_send(client, chat_id, f"❌ War Room error {r.status_code}: {r.text[:500]}")
            return
        meeting_id = r.json().get("meeting_id")
        if not meeting_id:
            await tg_send(client, chat_id, "❌ No meeting_id returned.")
            return
    except Exception as e:
        await tg_send(client, chat_id, f"❌ Failed to reach War Room: {e}")
        return

    _active[chat_id] = asyncio.create_task(track_meeting(chat_id, meeting_id))


async def main() -> None:
    log.info("SemeClaw Telegram bridge starting. API=%s allow=%s", API_BASE, ALLOWED or "ALL")
    offset: Optional[int] = None
    async with httpx.AsyncClient() as client:
        # Identity check
        try:
            me = await client.get(f"{TG}/getMe", timeout=10)
            log.info("Bot: %s", me.json())
        except Exception as e:
            log.error("getMe failed: %s", e)

        while True:
            try:
                params = {"timeout": 30, "allowed_updates": '["message"]'}
                if offset is not None:
                    params["offset"] = offset
                r = await client.get(f"{TG}/getUpdates", params=params, timeout=40)
                data = r.json()
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if msg:
                        asyncio.create_task(handle_message(client, msg))
            except Exception as e:
                log.warning("poll loop error: %s", e)
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
