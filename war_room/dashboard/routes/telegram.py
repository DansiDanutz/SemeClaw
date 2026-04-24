"""Telegram intervention webhook.

Lets users drive the 3-strike intervention loop straight from a Telegram chat.

POST /api/telegram/webhook
    Telegram Bot API → set this URL via:
       curl https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC>/api/telegram/webhook
    Optional: set TELEGRAM_WEBHOOK_SECRET; we verify the
    `X-Telegram-Bot-Api-Secret-Token` header.

Message grammar (everything case-insensitive on the command):
    /help                       → usage
    /comment <task_id> <text>   → intervene on the given task
    <task_id_prefix>: <text>    → shorthand (8+ char prefix is fine)
    /list                       → last 10 tasks for the default tenant

Each intervention reply is summarised back into the chat. On turn 3
the orchestrator decision + new dialog version are included.
"""
from __future__ import annotations

import html
import logging
import os

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from war_room.adapters.telegram import TelegramAdapter
from war_room.dashboard.routes.deps import _tenant_id
from war_room.dashboard.routes.meetings import create_legacy_meeting
from war_room.tasks import _db, intervene as _intervene

logger = logging.getLogger("war_room.dashboard.telegram")
router = APIRouter(tags=["telegram"])

_HELP = (
    "<b>SemeClaw War Room bot</b>\n\n"
    "Send a prompt to start a meeting, or comment on an existing task.\n\n"
    "<b>Commands</b>\n"
    "• <code>/run &lt;task&gt;</code> — start a meeting from free text\n"
    "• <code>/list</code> — recent tasks\n"
    "• <code>/comment &lt;task_id&gt; &lt;text&gt;</code> — intervene on a task\n"
    "• <code>&lt;task_id_prefix&gt;: &lt;text&gt;</code> — same, shorthand\n\n"
    "Plain text without a command is treated like <code>/run</code>.\n"
    "Turn 3 triggers the SemeClaw orchestrator (final decision + new dialog)."
)


async def _resolve_task_id(prefix: str) -> str | None:
    """Accept a full uuid or an 8+ char prefix; resolve via PostgREST ilike."""
    p = (prefix or "").strip().lower()
    if not p:
        return None
    if len(p) >= 32 and "-" in p:
        return p  # already a full uuid
    if len(p) < 8:
        return None
    try:
        from war_room.tasks._db import supa
        rows = await supa("get",
            f"semeclaw_tasks?id=ilike.{p}*&select=id&limit=2")
        if len(rows) == 1:
            return rows[0]["id"]
    except Exception as e:
        logger.warning("task lookup failed for %s: %s", p, e)
    return None


def _format_replies(result: dict) -> str:
    parts: list[str] = []
    turn = result.get("turn_index")
    parts.append(f"<b>Turn {turn}/3</b>")
    for r in result.get("agent_replies", []) or []:
        agent = r.get("agent_id", "?")
        text = (r.get("text") or "").strip()
        parts.append(f"<b>{html.escape(str(agent))}</b>: {html.escape(text)}")
    decision = result.get("orchestrator_decision")
    if decision:
        parts.append("")
        parts.append("<b>🧭 Orchestrator decision</b>")
        parts.append(html.escape(decision.get("rationale", "")))
        patch = decision.get("task_patch", {}) or {}
        if patch:
            kv = ", ".join(
                f"{k}={v!r}" for k, v in patch.items() if k != "description"
            )
            if kv:
                parts.append(f"<i>patch:</i> {html.escape(kv)}")
    nd = result.get("new_dialog")
    if nd:
        parts.append(f"📝 dialog v{nd.get('version')} composed")
    wb = result.get("writeback") or {}
    if wb.get("ok"):
        parts.append(f"⏪ writeback → {html.escape(str(wb.get('source','?')))} ok")
    elif wb:
        parts.append(
            f"⚠️ writeback → {html.escape(str(wb.get('source','?')))}: "
            f"{html.escape(str(wb.get('error','')))}"
        )
    return "\n".join(parts)


def _format_meeting(task: dict, dialog: dict) -> str:
    parts = [
        f"<b>Meeting ready</b> <code>{html.escape((task.get('id') or '')[:8])}</code>",
        f"Agents: {', '.join(html.escape(a) for a in (task.get('assigned_agents') or [])) or 'writer'}",
        "",
    ]
    for line in (dialog.get("lines") or []):
        text = (line.get("text") or "").strip()
        if not text:
            continue
        speaker = line.get("agent_id") or line.get("role") or "agent"
        parts.append(f"<b>{html.escape(str(speaker))}</b>: {html.escape(text)}")
    return "\n".join(parts)


async def _start_meeting(tg: TelegramAdapter, chat_id: int | str, request: Request, task_text: str) -> None:
    try:
        _, task, dialog = await create_legacy_meeting(
            task_text,
            tenant_id=_tenant_id(request),
            source="telegram",
            meta={"transport": "telegram-webhook", "chat_id": str(chat_id)},
        )
    except Exception as e:
        await tg.send_message(chat_id, f"⚠️ failed to start meeting: {html.escape(str(e))}")
        return
    await tg.send_message(chat_id, _format_meeting(task, dialog))


async def _handle_text(tg: TelegramAdapter, chat_id: int | str, text: str, request: Request) -> None:
    raw = (text or "").strip()
    if not raw:
        return

    low = raw.lower()
    if low in ("/help", "/start"):
        await tg.send_message(chat_id, _HELP)
        return

    if low.startswith("/run"):
        parts = raw.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            await tg.send_message(chat_id, "Usage: <code>/run &lt;task&gt;</code>")
            return
        await _start_meeting(tg, chat_id, request, parts[1].strip())
        return

    if low.startswith("/list"):
        try:
            rows = await _db.list_tasks(tenant_id=_tenant_id(request), limit=10)
        except Exception as e:
            await tg.send_message(chat_id, f"⚠️ list failed: {e}")
            return
        if not rows:
            await tg.send_message(chat_id, "No tasks yet. Run <code>semeclaw tasks sync</code>.")
            return
        lines = ["<b>Recent tasks</b>"]
        for t in rows:
            lines.append(f"<code>{t['id'][:8]}</code> {t.get('status','?')} — "
                         f"{(t.get('title') or '')[:50]}")
        await tg.send_message(chat_id, "\n".join(lines))
        return

    # Parse intervention forms.
    task_prefix = comment = ""
    if low.startswith("/comment"):
        parts = raw.split(None, 2)
        if len(parts) < 3:
            await tg.send_message(chat_id,
                "Usage: <code>/comment &lt;task_id&gt; &lt;text&gt;</code>")
            return
        task_prefix, comment = parts[1], parts[2]
    elif ":" in raw:
        head, _, tail = raw.partition(":")
        task_prefix, comment = head.strip(), tail.strip()
    else:
        await _start_meeting(tg, chat_id, request, raw)
        return

    if not comment:
        await tg.send_message(chat_id, "Empty comment — ignored.")
        return

    task_id = await _resolve_task_id(task_prefix)
    if not task_id:
        if not low.startswith("/comment"):
            await _start_meeting(tg, chat_id, request, raw)
            return
        await tg.send_message(chat_id,
            f"⚠️ Could not resolve task <code>{task_prefix}</code>. "
            "Use 8+ chars of the id, or <code>/list</code> to find it.")
        return

    try:
        result = await _intervene.intervene(task_id, comment)
    except Exception as e:
        await tg.send_message(chat_id, f"⚠️ intervene failed: {e}")
        return

    if not result.get("ok"):
        await tg.send_message(chat_id,
            f"⚠️ {result.get('error','intervene failed')}")
        return

    await tg.send_message(chat_id, _format_replies(result))


@router.get("/api/telegram/webhook")
async def telegram_webhook_probe():
    """Plain probe so you can curl the URL and confirm it's live."""
    has_token = bool(
        os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("SEMECLAW_BOT_TOKEN")
    )
    has_secret = bool(os.environ.get("TELEGRAM_WEBHOOK_SECRET"))
    return {"ok": True, "bot_configured": has_token, "secret_configured": has_secret}


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if expected and x_telegram_bot_api_secret_token != expected:
        return JSONResponse({"ok": False, "error": "bad secret"}, status_code=401)

    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    tg = TelegramAdapter()
    parsed = await tg.handle_update(update)
    if not parsed:
        return PlainTextResponse("ok")

    chat_id = parsed.get("chat_id")
    if chat_id is None:
        return PlainTextResponse("ok")

    # Reconstruct the user text so we can dispatch (handle_update only returns parsed cmd/args).
    msg = update.get("message") or update.get("edited_message") or {}
    text = msg.get("text", "") or ""

    try:
        await _handle_text(tg, chat_id, text, request)
    except Exception as e:
        logger.exception("telegram handler failed")
        try:
            await tg.send_message(chat_id, f"⚠️ internal error: {e}")
        except Exception:
            pass
    finally:
        await tg.close()

    return PlainTextResponse("ok")
