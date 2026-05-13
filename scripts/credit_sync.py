"""
Daily credit sync — reconcile NERVIX backend credits with AdClaw/Supabase.

Fetches credit allocations / usage from the NERVIX platform API and syncs
them into the AdClaw Supabase tables (adclaw_advertisers.wallet_credits).

Env vars:
    NERVIX_API_BASE         Base URL of nervix.ai API (e.g. https://nervix.ai/api/v1)
    NERVIX_API_KEY          Service API key for NERVIX
    DLS_TEAM_SUPABASE_URL   Supabase project URL
    DLS_TEAM_SUPABASE_SERVICE_KEY  Supabase service-role key
    SYNC_DRY_RUN            Set to "1" to preview changes without writing
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("credit_sync")

NERVIX_BASE = os.environ.get("NERVIX_API_BASE", "").rstrip("/")
NERVIX_KEY = os.environ.get("NERVIX_API_KEY", "").strip()
SUPA_URL = os.environ.get("DLS_TEAM_SUPABASE_URL", "").rstrip("/")
SUPA_KEY = os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "").strip()
DRY_RUN = os.environ.get("SYNC_DRY_RUN", "0") == "1"

_SUPA_HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


async def _supa(method: str, path: str, **kwargs) -> list[dict] | dict:
    async with httpx.AsyncClient(base_url=SUPA_URL, timeout=30.0) as c:
        r = await getattr(c, method)(f"/rest/v1/{path}", headers=_SUPA_HEADERS, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else []


async def fetch_nervix_credits() -> list[dict[str, Any]]:
    """Fetch advertiser credit balances from NERVIX platform."""
    if not NERVIX_BASE or not NERVIX_KEY:
        logger.warning("NERVIX_API_BASE or NERVIX_API_KEY not set — returning empty")
        return []

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(
            f"{NERVIX_BASE}/credits/balances",
            headers={"Authorization": f"Bearer {NERVIX_KEY}"},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("advertisers", data) if isinstance(data, dict) else data


async def fetch_adclaw_advertisers() -> list[dict[str, Any]]:
    return await _supa("get", "adclaw_advertisers?select=*")


async def sync_credits() -> dict[str, int]:
    """Reconcile and return stats."""
    stats = {"matched": 0, "created": 0, "updated": 0, "errors": 0}

    nervix_rows = await fetch_nervix_credits()
    adclaw_rows = await fetch_adclaw_advertisers()
    adclaw_by_email = {a["email"]: a for a in adclaw_rows}

    for row in nervix_rows:
        email = row.get("email", "").lower().strip()
        if not email:
            continue

        credits = int(row.get("credits", 0))
        existing = adclaw_by_email.get(email)

        if existing:
            delta = credits - existing.get("wallet_credits", 0)
            if delta == 0:
                stats["matched"] += 1
                continue

            if DRY_RUN:
                logger.info("[DRY RUN] Would update %s: %d -> %d", email, existing["wallet_credits"], credits)
                stats["updated"] += 1
                continue

            try:
                await _supa(
                    "patch",
                    f'adclaw_advertisers?id=eq.{existing["id"]}',
                    json={"wallet_credits": credits, "updated_at": datetime.now(timezone.utc).isoformat()},
                )
                logger.info("Updated %s: %d -> %d", email, existing["wallet_credits"], credits)
                stats["updated"] += 1
            except Exception as exc:
                logger.error("Failed to update %s: %s", email, exc)
                stats["errors"] += 1
        else:
            if DRY_RUN:
                logger.info("[DRY RUN] Would create advertiser %s with %d credits", email, credits)
                stats["created"] += 1
                continue

            try:
                await _supa(
                    "post",
                    "adclaw_advertisers",
                    json={
                        "email": email,
                        "display_name": row.get("name", email.split("@")[0]),
                        "wallet_credits": credits,
                    },
                )
                logger.info("Created advertiser %s with %d credits", email, credits)
                stats["created"] += 1
            except Exception as exc:
                logger.error("Failed to create %s: %s", email, exc)
                stats["errors"] += 1

    return stats


async def main() -> int:
    if not SUPA_URL or not SUPA_KEY:
        logger.error("Supabase credentials missing")
        return 1

    if DRY_RUN:
        logger.info("=== DRY RUN MODE ===")

    logger.info("Starting credit sync: NERVIX -> AdClaw")
    stats = await sync_credits()
    logger.info("Sync complete: %s", stats)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
