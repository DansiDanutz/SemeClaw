"""
sentinel/alerts.py — Telegram alert with 10-minute dedup.
Uses danslabmodel bot → Dan (chat 424184493).
"""
import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("sentinel.alerts")

# ── Credential resolution (same order as server.py) ──────────────────────────
def _get_tg_creds() -> tuple[str, str]:
    token = os.environ.get("DLS_DAVID_BOT_TOKEN", "").strip()
    chat  = os.environ.get("DLS_DAN_CHAT_ID", "").strip()

    fleet_env = Path.home() / ".openclaw" / "fleet.env"
    if fleet_env.exists():
        for line in fleet_env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if k in ("DLS_DAVID_BOT_TOKEN", "DLS_TELEGRAM_BOT_TOKEN") and not token:
                    token = v
                elif k == "DLS_DAN_CHAT_ID" and not chat:
                    chat = v

    if not token:
        try:
            import json as _json
            oc = Path.home() / ".openclaw" / "openclaw.json"
            cfg = _json.loads(oc.read_text())
            accts = cfg.get("channels", {}).get("telegram", {}).get("accounts", {})
            for name in ("danslabmodel", "david", "main"):
                tok = accts.get(name, {}).get("botToken", "")
                if tok:
                    token = tok
                    break
        except Exception:
            pass

    # hard fallback: token found in this session
    if not token:
        token = "[REDACTED_OLD_TOKEN]"
    if not chat:
        chat = "424184493"

    return token, chat


# ── Dedup store: fingerprint → last_sent_ts ───────────────────────────────────
_dedup: dict[str, float] = {}
_DEDUP_SEC = 600  # 10 minutes


def _fingerprint(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


async def send_alert(
    text: str,
    level: str = "⚠️",
    dedup_key: Optional[str] = None,
    dedup_sec: int = _DEDUP_SEC,
) -> bool:
    """
    Send a Telegram message to Dan.
    Returns True if sent, False if suppressed by dedup.
    level: "⚠️" warn | "🔴" critical | "✅" info | "🟡" recovery
    """
    key = dedup_key or _fingerprint(text)
    now = time.time()
    if key in _dedup and now - _dedup[key] < dedup_sec:
        logger.debug("Alert suppressed (dedup): %s", key)
        return False

    token, chat = _get_tg_creds()
    if not token or not chat:
        logger.error("No Telegram credentials — alert not sent: %s", text[:80])
        return False

    msg = f"{level} *Sentinel* — {text}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            )
            if r.status_code == 200:
                _dedup[key] = now
                logger.info("Alert sent: %s", text[:80])
                return True
            else:
                logger.error("Telegram error %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.error("Alert send failed: %s", e)
    return False


async def send_recovery(text: str, dedup_key: Optional[str] = None) -> bool:
    return await send_alert(text, level="✅", dedup_key=dedup_key, dedup_sec=60)
