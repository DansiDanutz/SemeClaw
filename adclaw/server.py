"""
AdClaw — Central Advertising Server for SemeClaw War Room loading screens.

Architecture (anti-bypass):
  War Room instances proxy /api/meeting/loading-slides → this server.
  Impressions are logged here atomically on slide fetch — before the client
  renders anything. No client-side reporting = impossible to fake from forks.
  Advertisers who remove SEMECLAW_ADS_URL get local fallback slides (no views).

Run:
  uvicorn adclaw.server:app --host 0.0.0.0 --port 8766

Env vars:
  DLS_TEAM_SUPABASE_URL          Supabase project URL
  DLS_TEAM_SUPABASE_SERVICE_KEY  Supabase service-role key
  ADCLAW_SECRET                  HMAC secret for signed tokens
  ADCLAW_CREDITS_PER_IMPRESSION  Credits to deduct per view (default: 1)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
except ImportError:
    raise SystemExit("Install: pip install fastapi uvicorn httpx")

logger = logging.getLogger("adclaw")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_SUPA_URL = os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip()
_SUPA_KEY = os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "").strip()
_ADCLAW_SECRET = os.environ.get("ADCLAW_SECRET", secrets.token_hex(32))
CREDITS_PER_IMPRESSION: int = int(os.environ.get("ADCLAW_CREDITS_PER_IMPRESSION", "1"))

# Credit pricing tiers
CREDITS_PER_USD_STANDARD = 10  # $1 = 10 credits
CREDITS_PER_USD_SUB = 15  # $50/mo subscription: 750 credits (15/USD)
SUBSCRIPTION_PRICE_USD = 50
SUBSCRIPTION_CREDITS = SUBSCRIPTION_PRICE_USD * CREDITS_PER_USD_SUB  # 750

_SUPA_HEADERS = {
    "apikey": _SUPA_KEY,
    "Authorization": f"Bearer {_SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ---------------------------------------------------------------------------
# Supabase helper
# ---------------------------------------------------------------------------


async def _supa(method: str, path: str, **kwargs):
    if not _SUPA_URL or not _SUPA_KEY:
        raise RuntimeError("Supabase not configured")
    async with httpx.AsyncClient(base_url=_SUPA_URL, timeout=8.0) as c:
        r = await getattr(c, method)(f"/rest/v1/{path}", headers=_SUPA_HEADERS, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else []


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="AdClaw", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Instance registry
# ---------------------------------------------------------------------------


@app.post("/api/instances/register")
async def register_instance(request: Request):
    """War Room instances call this on startup to announce themselves."""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    tenant_id = body.get("tenant_id", "default")
    public_url = body.get("public_url", "")

    if not instance_id:
        return JSONResponse({"error": "instance_id required"}, status_code=400)

    try:
        await _supa(
            "post",
            "adclaw_instances",
            json={
                "instance_id": instance_id,
                "tenant_id": tenant_id,
                "public_url": public_url,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            params={"on_conflict": "instance_id"},
            headers={**_SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        )
    except Exception as e:
        logger.warning("Instance upsert failed: %s", e)

    return JSONResponse({"ok": True, "instance_id": instance_id})


# ---------------------------------------------------------------------------
# Slide serving — impression logged atomically on fetch
# ---------------------------------------------------------------------------


@app.get("/api/slides/next")
async def get_next_slide(
    instance_id: str,
    ip_hash: str = "",
    tenant_id: str = "default",
    count: int = 5,
):
    """Return up to `count` active slides and log impressions immediately.

    The impression is written to Supabase before this response is sent.
    There is no second "confirm" step — the view is counted on fetch.
    Credits are deducted from the campaign wallet atomically.

    Frequency capping: same IP cannot see the same slide more than 3 times
    in any 4-hour rolling window.
    """
    # 1. Fetch enabled slides — Diamond priority-boosted cards rank first,
    #    then least-recently-served (round-robin) within each group.
    try:
        slides_raw = await _supa(
            "get",
            "adclaw_slides?status=eq.enabled&order=priority_boost.desc,last_served_at.asc.nullsfirst&limit=50",
        )
    except Exception as e:
        logger.error("Slide fetch failed: %s", e)
        return JSONResponse({"error": "unavailable"}, status_code=503)

    if not slides_raw:
        return JSONResponse({"slides": [], "token": None, "wait_ms": 0})

    # 2. Frequency cap — exclude slides this IP has seen ≥3 times in last 4h
    eligible_slides = slides_raw
    if ip_hash and ip_hash != "unknown":
        try:
            capped_rows = await _supa(
                "post",
                "rpc/adclaw_frequency_cap",
                json={
                    "p_ip_hash": ip_hash,
                    "p_hours": 4,
                    "p_max_views": 3,
                },
            )
            capped_ids = {r["slide_id"] for r in capped_rows}
            if capped_ids:
                eligible_slides = [s for s in slides_raw if s["id"] not in capped_ids]
                logger.info(
                    "Frequency cap excluded %d slide(s) for ip_hash=%s...",
                    len(capped_ids),
                    ip_hash[:8],
                )
        except Exception as e:
            logger.warning("Frequency cap lookup failed: %s", e)
            # On failure, serve all slides (fail-open for ad delivery)

    # 3. Take up to count from eligible slides
    served = eligible_slides[: max(1, min(count, 10))]
    if not served:
        return JSONResponse({"slides": [], "token": None, "wait_ms": 0})

    now_iso = datetime.now(timezone.utc).isoformat()

    # 4. Log impressions + deduct credits only for actually served slides
    import asyncio

    await asyncio.gather(
        *[
            _record_impression(
                slide_id=slide["id"],
                campaign_id=slide["campaign_id"],
                instance_id=instance_id,
                ip_hash=ip_hash,
                tenant_id=tenant_id,
                now_iso=now_iso,
            )
            for slide in served
        ],
        return_exceptions=True,
    )

    # Normalise slide shape for the War Room client
    client_slides = [_normalise_slide(s) for s in served]

    # Issue a signed token the War Room can echo back for extra verification
    token = _issue_token(instance_id)

    return JSONResponse(
        {
            "tier": "free",
            "slides": client_slides,
            "token": token,
            "source": "adclaw",
        }
    )


# ---------------------------------------------------------------------------
# Impression write durability
#
# _record_impression used to wrap three Supabase calls in a single try/except
# that silently swallowed any error. Under even transient Supabase flakiness
# that meant: slide served -> impression row missing -> no credit deducted.
# The "anti-bypass" claim in the module docstring quietly broke.
#
# Hardened flow:
#   1. Retry each write with exponential backoff (3 attempts total).
#   2. On final failure, append to an on-disk dead-letter log so a cron /
#      operator can replay the write when Supabase is healthy again.
#   3. Serving stays fail-open (we still return the slide) so ad delivery
#      isn't held hostage to Supabase availability — but we no longer lose
#      the audit trail.
# ---------------------------------------------------------------------------
import asyncio

_IMPRESSION_DLQ_PATH = Path(os.environ.get("ADCLAW_DLQ_PATH", "/app/data/adclaw_dlq.jsonl")).expanduser()
_IMPRESSION_MAX_ATTEMPTS = int(os.environ.get("ADCLAW_IMPRESSION_RETRIES", "3"))
_IMPRESSION_BACKOFF_BASE_S = 0.1
_IMPRESSION_BACKOFF_CAP_S = 2.0


async def _supa_with_retry(method: str, path: str, attempts: int = _IMPRESSION_MAX_ATTEMPTS, **kwargs):
    """_supa wrapper with bounded exponential backoff. Re-raises on final failure."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await _supa(method, path, **kwargs)
        except Exception as exc:  # noqa: BLE001 — retry any error
            last_exc = exc
            if i == attempts - 1:
                break
            delay = min(_IMPRESSION_BACKOFF_CAP_S, _IMPRESSION_BACKOFF_BASE_S * (2**i))
            await asyncio.sleep(delay)
    assert last_exc is not None  # for type-checkers
    raise last_exc


def _dlq_append(entry: dict) -> None:
    """Append a failed-impression entry to the local dead-letter log.

    Best-effort: if the DLQ path is unwritable we log and move on rather than
    crash the response path.
    """
    try:
        _IMPRESSION_DLQ_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _IMPRESSION_DLQ_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.error("DLQ write failed (%s); impression lost: %s", e, entry)


async def _record_impression(
    slide_id: str,
    campaign_id: str,
    instance_id: str,
    ip_hash: str,
    tenant_id: str,
    now_iso: str,
) -> None:
    """Write impression + deduct credits + stamp last_served_at, durably.

    Each step retries on transient Supabase errors; on exhaustion the step's
    payload is written to the DLQ so no impression is lost silently.
    """
    impression_payload = {
        "slide_id": slide_id,
        "campaign_id": campaign_id,
        "instance_id": instance_id,
        "ip_hash": ip_hash or "unknown",
        "tenant_id": tenant_id,
        "viewed_at": now_iso,
    }
    deduct_payload = {
        "p_campaign_id": campaign_id,
        "p_credits": CREDITS_PER_IMPRESSION,
        "p_slide_id": slide_id,
        "p_instance_id": instance_id,
        "p_description": f"View via {instance_id or 'unknown'}",
    }
    last_served_payload = {"last_served_at": now_iso}

    # 1. Impression row — critical for audit / billing.
    try:
        await _supa_with_retry("post", "adclaw_impressions", json=impression_payload)
    except Exception as e:  # noqa: BLE001
        logger.error("impression insert exhausted retries for slide %s: %s", slide_id, e)
        _dlq_append({"kind": "impression", "at": now_iso, "payload": impression_payload, "error": str(e)})

    # 2. Credit deduction — atomic in Postgres; skip on failure to avoid double-charging on retry.
    try:
        await _supa_with_retry("post", "rpc/adclaw_deduct_credits", json=deduct_payload)
    except Exception as e:  # noqa: BLE001
        logger.error("deduct_credits exhausted retries for campaign %s: %s", campaign_id, e)
        _dlq_append({"kind": "deduct", "at": now_iso, "payload": deduct_payload, "error": str(e)})

    # 3. last_served_at — purely for rotation fairness; DLQ is overkill, just log.
    try:
        await _supa_with_retry(
            "patch",
            f"adclaw_slides?id=eq.{slide_id}",
            json=last_served_payload,
            attempts=2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("last_served_at update failed for slide %s: %s", slide_id, e)


def _normalise_slide(row: dict) -> dict:
    """Map Supabase row → War Room slide schema."""
    return {
        "id": row.get("id"),
        "type": row.get("slide_type", "feature"),
        "badge": row.get("badge", ""),
        "accent": row.get("accent_color", "#f59e0b"),
        "headline": row.get("headline", ""),
        "body": row.get("body", ""),
        "cta_label": row.get("cta_label"),
        "cta_url": row.get("cta_url"),
    }


# ---------------------------------------------------------------------------
# Advertiser wallet endpoints
# ---------------------------------------------------------------------------


@app.get("/api/advertiser/{advertiser_id}/wallet")
async def get_wallet(advertiser_id: str):
    try:
        rows = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=id,email,wallet_credits")
        if not rows:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(rows[0])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/advertiser/{advertiser_id}/topup")
async def topup_wallet(advertiser_id: str, request: Request):
    """Add credits to an advertiser's wallet. Called by Stripe webhook after payment."""
    body = await request.json()
    credits = int(body.get("credits", 0))
    if credits <= 0:
        return JSONResponse({"error": "credits must be > 0"}, status_code=400)
    try:
        await _supa(
            "post",
            "rpc/adclaw_topup_credits",
            json={
                "p_advertiser_id": advertiser_id,
                "p_credits": credits,
            },
        )
        return JSONResponse({"ok": True, "credits_added": credits})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/campaign/{campaign_id}/stats")
async def campaign_stats(campaign_id: str):
    """Return view count + credit burn for a campaign."""
    try:
        rows = await _supa(
            "get",
            f"adclaw_impressions?campaign_id=eq.{campaign_id}&select=id",
        )
        credit_rows = await _supa(
            "get",
            f"adclaw_campaigns?id=eq.{campaign_id}&select=id,name,budget_credits,spent_credits,status",
        )
        return JSONResponse(
            {
                "total_views": len(rows),
                "campaign": credit_rows[0] if credit_rows else None,
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Token signing (so War Room can verify slide response came from AdClaw)
# ---------------------------------------------------------------------------


def _issue_token(instance_id: str) -> str:
    wall = int(time.time())
    msg = f"{instance_id}:{wall}".encode()
    sig = hmac.new(_ADCLAW_SECRET.encode(), msg, "sha256").hexdigest()[:20]
    return f"{wall}.{sig}"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"ok": True, "service": "adclaw", "version": "1.0.0"}


if __name__ == "__main__":
    port = int(os.environ.get("ADCLAW_PORT", "8766"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
