"""Health, TTS, and board routes."""

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import CONFIG_FILE, STATE_FILE

router = APIRouter(tags=["health"])


try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


@router.get("/api/board")
async def api_board():
    """Return Paperclip board state via the bridge."""
    paperclip_url = "http://127.0.0.1:3100"
    use_mock = True
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            paperclip_url = cfg.get("paperclip_url", paperclip_url)
            use_mock = cfg.get("mock", True)
        except Exception:
            pass

    # Try live API
    if not use_mock and _HAS_HTTPX:
        try:
            async with httpx.AsyncClient(base_url=paperclip_url, timeout=5.0) as client:
                r = await client.get("/health")
                r.raise_for_status()
                # Try to get board
                try:
                    r = await client.get("/board")
                    r.raise_for_status()
                    return JSONResponse(r.json())
                except Exception:
                    pass
        except Exception:
            pass

    # Fallback: return from state file or mock
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    return JSONResponse(
        state.get(
            "paperclip",
            {
                "connected": False,
                "mode": "mock",
                "agents": {
                    "Dexter": {"role": "Research", "status": "idle"},
                    "David": {"role": "Architect", "status": "idle"},
                    "Memo": {"role": "Strategist", "status": "idle"},
                    "Hermes": {"role": "Writer", "status": "idle"},
                    "Sienna": {"role": "Crypto", "status": "idle"},
                    "Nano": {"role": "AgentCreator", "status": "idle"},
                },
            },
        )
    )


@router.get("/api/health/deep")
async def api_health_deep():
    """Deep health check: reports each subsystem.

    Returns:
        {
            "ok": overall boolean,
            "status": "ok" | "degraded" | "down",
            "checks": {
                "supabase": {...}, "stripe": {...}, "tts": {...},
                "dlq": {...}, "disk": {...}, "version": {...},
            },
            "ts": "<iso-8601>",
        }

    Never raises — any subsystem check failure is reflected in that
    subsystem's status field. Use for alerting; the shallow health check
    at ``/api/agent/health`` stays cheap for Fly's TCP/HTTP healthcheck.
    """
    import asyncio
    import os
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path as _P

    import httpx

    from war_room.dashboard.routes.deps import (
        APP_VERSION,
        SEMECLAW_TENANT_ID,
        STRIPE_SECRET_KEY,
        SUPA_HEADERS,
        SUPA_KEY,
        SUPA_URL,
    )

    async def _check_supabase() -> dict:
        if not SUPA_URL or not SUPA_KEY:
            return {"status": "unconfigured", "ok": False, "detail": "env missing"}
        try:
            async with httpx.AsyncClient(base_url=SUPA_URL, timeout=3.0) as c:
                r = await c.get("/rest/v1/", headers=SUPA_HEADERS)
                return {
                    "status": "ok" if r.status_code < 500 else "degraded",
                    "ok": r.status_code < 500,
                    "http": r.status_code,
                }
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "ok": False, "detail": str(exc)[:120]}

    async def _check_stripe() -> dict:
        if not STRIPE_SECRET_KEY:
            return {"status": "unconfigured", "ok": True, "detail": "Stripe disabled"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(
                    "https://api.stripe.com/v1/charges?limit=1",
                    auth=(STRIPE_SECRET_KEY, ""),
                )
                return {
                    "status": "ok" if r.status_code < 500 else "degraded",
                    "ok": r.status_code < 500,
                    "http": r.status_code,
                }
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "ok": False, "detail": str(exc)[:120]}

    def _check_tts() -> dict:
        engines = {
            "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY", "").strip()),
            "edge_tts": True,  # no key required
            "kokoro": True,  # bundled fallback
        }
        any_configured = any(engines.values())
        return {
            "status": "ok" if any_configured else "degraded",
            "ok": any_configured,
            "engines": engines,
        }

    def _check_dlq() -> dict:
        paths = {
            "adclaw": os.environ.get("ADCLAW_DLQ_PATH", "/app/data/adclaw_dlq.jsonl"),
            "tasks": os.environ.get("SEMECLAW_TASKS_DLQ_PATH", "/app/data/semeclaw_tasks_dlq.jsonl"),
        }
        sizes: dict[str, object] = {}
        total_bytes = 0
        for name, p in paths.items():
            pp = _P(p)
            if pp.exists():
                try:
                    sz = pp.stat().st_size
                    sizes[name] = {"path": p, "bytes": sz, "exists": True}
                    total_bytes += sz
                except Exception as exc:  # noqa: BLE001
                    sizes[name] = {"path": p, "error": str(exc)[:80]}
            else:
                sizes[name] = {"path": p, "exists": False, "bytes": 0}
        # 10 MiB total across DLQs is the threshold at which we degrade.
        threshold = int(os.environ.get("SEMECLAW_DLQ_WARN_BYTES", str(10 * 1024 * 1024)))
        return {
            "status": "ok" if total_bytes < threshold else "degraded",
            "ok": True,  # DLQs existing is informational, not a failure
            "total_bytes": total_bytes,
            "threshold_bytes": threshold,
            "queues": sizes,
        }

    def _check_disk() -> dict:
        target = os.environ.get("SEMECLAW_DATA_DIR", "/app/data")
        try:
            usage = shutil.disk_usage(target)
            pct_used = usage.used / usage.total if usage.total else 0.0
            ok = pct_used < 0.9
            return {
                "status": "ok" if ok else "degraded",
                "ok": ok,
                "path": target,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "pct_used": round(pct_used, 3),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "unknown", "ok": True, "detail": str(exc)[:120]}

    supabase, stripe_ = await asyncio.gather(_check_supabase(), _check_stripe())
    tts = _check_tts()
    dlq = _check_dlq()
    disk = _check_disk()
    checks = {
        "supabase": supabase,
        "stripe": stripe_,
        "tts": tts,
        "dlq": dlq,
        "disk": disk,
        "version": {"status": "ok", "ok": True, "app": APP_VERSION, "tenant": SEMECLAW_TENANT_ID},
    }

    if all(c.get("ok", False) for c in checks.values()):
        overall = "ok"
    elif any(c.get("status") == "down" for c in checks.values()):
        overall = "down"
    else:
        overall = "degraded"

    from fastapi.responses import JSONResponse as _J

    http_status = 200 if overall == "ok" else 503 if overall == "down" else 200
    return _J(
        {
            "ok": overall == "ok",
            "status": overall,
            "checks": checks,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        status_code=http_status,
    )
