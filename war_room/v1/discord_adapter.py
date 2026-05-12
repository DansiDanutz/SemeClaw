"""Discord task adapter — reference implementation for the v1 contract.

Every adapter must expose:
  - ``probe()``   -> dict with required env vars + their state (for `semeclaw doctor`)
  - ``ingest()``  -> async iterator of task dicts
  - ``writeback()`` -> async coroutine that pushes an orchestrator decision back

This module talks to Discord via the bot HTTP API (``https://discord.com/api/v10``).
It only uses the channel ``GET /channels/{id}/messages`` + ``POST messages``
endpoints — no WebSocket gateway connection — so it runs cleanly inside
the FastAPI worker.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger("semeclaw.v1.discord")

API_BASE = "https://discord.com/api/v10"
TASK_PREFIX = "!task "
USER_AGENT = "SemeClaw-DiscordAdapter/1.0"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: str
    channel_id: str
    tenant_id: str

    @classmethod
    def from_env(cls) -> DiscordConfig | None:
        token = _env("DISCORD_BOT_TOKEN")
        channel = _env("DISCORD_CHANNEL_ID")
        if not token or not channel:
            return None
        return cls(bot_token=token, channel_id=channel, tenant_id=_env("SEMECLAW_TENANT_ID") or "default")


def probe() -> dict:
    cfg = DiscordConfig.from_env()
    return {
        "id": "discord",
        "ok": cfg is not None,
        "required_env": ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"],
        "missing": [k for k in ("DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID") if not _env(k)],
        "remediation": (
            "Create a bot at https://discord.com/developers/applications, copy the token to "
            "DISCORD_BOT_TOKEN, invite it to the target channel, and set DISCORD_CHANNEL_ID."
        ),
    }


def _headers(cfg: DiscordConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bot {cfg.bot_token}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }


def _message_to_task(cfg: DiscordConfig, msg: dict) -> dict | None:
    content = (msg.get("content") or "").strip()
    if not content.startswith(TASK_PREFIX):
        return None
    # Split into (first line, remainder) from the same normalised buffer so
    # title truncation and leading whitespace can't leak into the description.
    after_prefix = content[len(TASK_PREFIX) :].lstrip()
    head, _, tail = after_prefix.partition("\n")
    full_title = head.strip()
    if not full_title:
        return None
    title = full_title[:120]
    overflow = full_title[120:].strip()
    description_parts = [p for p in (overflow, tail.strip()) if p]
    description = "\n".join(description_parts)
    return {
        "source": "discord",
        "source_id": msg["id"],
        "tenant_id": cfg.tenant_id,
        "title": title,
        "description": description,
        "status": "open",
        "assigned_agents": [],
        "meta": {
            "discord_channel_id": cfg.channel_id,
            "discord_author": (msg.get("author") or {}).get("username", ""),
            "discord_message_id": msg["id"],
        },
    }


async def ingest(limit: int = 25) -> AsyncIterator[dict]:
    """Yield task dicts for any ``!task ...`` message in the configured channel."""
    cfg = DiscordConfig.from_env()
    if cfg is None:
        return
    url = f"{API_BASE}/channels/{cfg.channel_id}/messages?limit={max(1, min(int(limit), 100))}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(url, headers=_headers(cfg))
        except httpx.HTTPError as exc:
            logger.warning("discord ingest transport error: %s", exc)
            return
        if r.status_code >= 400:
            logger.warning("discord ingest HTTP %s: %s", r.status_code, r.text[:200])
            return
        try:
            messages = r.json()
        except ValueError as exc:
            logger.warning("discord ingest non-JSON response: %s", exc)
            return
        for msg in messages:
            t = _message_to_task(cfg, msg)
            if t is not None:
                yield t


async def writeback(task: dict, decision: dict) -> dict:
    """Post the orchestrator's decision as a threaded reply on the source message.

    Falls back to a normal channel message if a reference can't be reconstructed.
    """
    cfg = DiscordConfig.from_env()
    if cfg is None:
        return {"ok": False, "error": "discord not configured"}

    meta = task.get("meta") or {}
    msg_id = meta.get("discord_message_id")
    rationale = (decision.get("rationale") or "").strip()
    status = (decision.get("task_patch") or {}).get("status", task.get("status"))
    title = (decision.get("task_patch") or {}).get("title", task.get("title"))
    body_lines = [
        f"**SemeClaw decision** for `{title}`",
        f"Status -> `{status}`",
    ]
    if rationale:
        body_lines.append(f"> {rationale}")
    body = {"content": "\n".join(body_lines)[:1900]}
    if msg_id:
        body["message_reference"] = {"message_id": msg_id, "channel_id": cfg.channel_id}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(
                f"{API_BASE}/channels/{cfg.channel_id}/messages",
                headers=_headers(cfg),
                json=body,
            )
        except httpx.HTTPError as exc:
            logger.warning("discord writeback transport error: %s", exc)
            return {"ok": False, "error": f"transport: {exc}"}
        discord_id: str | None = None
        if r.content:
            try:
                discord_id = (r.json() or {}).get("id")
            except ValueError:
                discord_id = None
        return {"ok": r.status_code < 400, "status": r.status_code, "discord_id": discord_id}
