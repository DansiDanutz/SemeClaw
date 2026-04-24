"""
War Room Dashboard — FastAPI + WebSocket server (port 8765)

Replaces the Flask polling dashboard with real-time WebSocket updates.
Clients connect via WebSocket and receive push notifications when:
- A new task run completes
- New research reports are created
- Paperclip board state changes
- Agent status changes

Run:
  python war_room/dashboard/server.py

API endpoints:
  GET  /              — Dashboard HTML
  GET  /api/state     — Current state
  GET  /api/reports   — Research reports
  GET  /api/logs      — Run logs
  GET  /api/agents    — Agent definitions
  POST /api/run       — Start a task
  GET  /api/board     — Paperclip board state
  WS   /ws            — WebSocket for real-time updates
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Optional

# Ensure repo root is on path so war_room imports work when run directly
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import AdClaw slide serving so we can run it in-process when no external AdClaw is configured
try:
    from adclaw.server import get_next_slide as _adclaw_get_next_slide
except Exception as _adclaw_import_err:
    _adclaw_get_next_slide = None  # type: ignore
    logger.warning("AdClaw module not available for in-process serving: %s", _adclaw_import_err)

# FastAPI — installed with: pip install fastapi uvicorn websockets
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("FastAPI not installed. Run: pip install fastapi uvicorn")
    sys.exit(1)


from war_room.dashboard.websocket_manager import manager
from war_room.dashboard.meeting_log import meeting_log
manager.set_before_broadcast_hook(meeting_log.record)
# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SEMECLAW_API_KEY = os.environ.get("SEMECLAW_API_KEY", "").strip()
SEMECLAW_CORS_ORIGINS = os.environ.get("SEMECLAW_CORS_ORIGINS", "*")
SEMECLAW_FRAME_ANCESTORS = os.environ.get("SEMECLAW_FRAME_ANCESTORS", "*")
SEMECLAW_TENANT_ID = os.environ.get("SEMECLAW_TENANT_ID", "default")
SEMECLAW_PUBLIC_URL = os.environ.get("SEMECLAW_PUBLIC_URL", "http://127.0.0.1:8765")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR = Path(os.environ.get("WAR_ROOM_DIR", str(Path(__file__).parent.parent)))
STATE_FILE   = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR     = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR   = WAR_ROOM_DIR / "agents"
CONFIG_FILE  = WAR_ROOM_DIR / "config.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_CHATS_FILE = WAR_ROOM_DIR / ".telegram_chats.json"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")

# ---------------------------------------------------------------------------
# Demo mode: inject demo agents when DEMO_MODE env var is set
# ---------------------------------------------------------------------------
import os as _os
if _os.environ.get("DEMO_MODE"):
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from demo.loader import DEMO_AGENTS, load_demo_into_war_room
        _DEMO_AGENTS = DEMO_AGENTS
        load_demo_into_war_room(WAR_ROOM_DIR)
        logger.info("Demo mode active — %d demo agents loaded", len(_DEMO_AGENTS))
    except Exception as _e:
        logger.warning("Demo loader failed: %s", _e)
        _DEMO_AGENTS = []
else:
    _DEMO_AGENTS = []

# ---------------------------------------------------------------------------
# Supabase — Moltbot project (okgwzwdtuhhpoyxyprzg)
# Credentials loaded from environment / ~/.openclaw/fleet.env — NEVER hardcoded.
# ---------------------------------------------------------------------------
def _load_supa_creds() -> tuple[str, str]:
    """Load Supabase URL + service-role key.
    Resolution order:
      1. Env vars  DLS_TEAM_SUPABASE_URL / DLS_TEAM_SUPABASE_SERVICE_KEY
      2. ~/.openclaw/fleet.env
    Raises RuntimeError if not found (server will fail fast rather than use wrong creds).
    """
    import os as _os
    url = _os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip()
    key = _os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        fe = Path.home() / ".openclaw" / "fleet.env"
        if fe.exists():
            for line in fe.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    # strip shell 'export ' prefix
                    if k.startswith("export "):
                        k = k[7:].strip()
                    if k == "DLS_TEAM_SUPABASE_URL" and not url:
                        url = v
                    elif k == "DLS_TEAM_SUPABASE_SERVICE_KEY" and not key:
                        key = v

    if not url or not key:
        raise RuntimeError(
            "Supabase credentials not found. "
            "Set DLS_TEAM_SUPABASE_URL and DLS_TEAM_SUPABASE_SERVICE_KEY "
            "in environment or ~/.openclaw/fleet.env"
        )
    return url, key


try:
    SUPA_URL, SUPA_KEY = _load_supa_creds()
except RuntimeError as _e:
    logger.warning("⚠ %s — Supabase features will be disabled", _e)
    SUPA_URL, SUPA_KEY = "", ""

SUPA_HEADERS = {
    "apikey":        SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


async def _supa(method: str, path: str, **kwargs):
    """Generic Supabase REST helper."""
    async with httpx.AsyncClient(base_url=SUPA_URL, timeout=10.0) as c:
        r = await getattr(c, method)(f"/rest/v1/{path}", headers=SUPA_HEADERS, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else []


async def _prune_agent_history(agent_name: str):
    """Keep only the 100 most recent terminal (success/failed) rows per agent."""
    try:
        rows = await _supa(
            "get",
            f"agent_run_history?agent_name=eq.{agent_name}"
            "&status=in.(success,failed)"
            "&order=created_at.desc&select=id",
        )
        ids_to_delete = [r["id"] for r in rows[100:]]
        if ids_to_delete:
            id_csv = "(" + ",".join(str(i) for i in ids_to_delete) + ")"
            await _supa("delete", f"agent_run_history?id=in.{id_csv}")
    except Exception as e:
        logger.warning("_prune_agent_history: %s", e)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
from war_room.dashboard.routes.deps import APP_VERSION


def _range_audio_response(
    data: bytes,
    request: "Request",
    *,
    media_type: str = "audio/mpeg",
    extra_headers: dict | None = None,
):
    """Return audio bytes with Range support so clients can scrub/resume.

    The HTTP Range protocol supported here is the 99% case: single byte-range
    ``Range: bytes=<start>-<end>`` or ``Range: bytes=<start>-``. Multipart
    ranges are not supported; such a request falls back to a 200 with the
    whole body (still valid per RFC 7233 §3.1).
    """
    from fastapi.responses import Response as _FR
    total = len(data)
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
    }
    if extra_headers:
        headers.update(extra_headers)

    rng = request.headers.get("range") or request.headers.get("Range") or ""
    if not rng or not rng.lower().startswith("bytes="):
        return _FR(content=data, media_type=media_type, headers=headers)

    try:
        spec = rng.split("=", 1)[1].strip()
        if "," in spec:  # multipart — decline; return whole body
            return _FR(content=data, media_type=media_type, headers=headers)
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total - 1
        if start < 0 or end >= total or start > end:
            raise ValueError("bad range")
    except Exception:
        # Malformed Range — RFC says return 416, but 200 with full body is a
        # safer fallback for players that send weird range headers.
        return _FR(content=data, media_type=media_type, headers=headers)

    slice_bytes = data[start : end + 1]
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    headers["Content-Length"] = str(len(slice_bytes))
    return _FR(content=slice_bytes, media_type=media_type, status_code=206, headers=headers)


async def _prune_loop() -> None:
    while True:
        try:
            removed = _prune_old_meetings()
            if removed:
                logger.info(f"meeting prune: removed {removed} files older than {MEETING_RETENTION_HOURS}h")
        except Exception as e:
            logger.warning(f"meeting prune failed: {e}")
        await asyncio.sleep(3600)


def _startup_config_checks() -> None:
    """Log loud warnings when critical env vars are missing.

    Called once at lifespan startup. Non-fatal — the app still boots so a
    deployer can see the boot succeed and check logs — but the WARNING lines
    are hard to miss in Fly logs / Grafana.
    """
    if STRIPE_SECRET_KEY and not STRIPE_WEBHOOK_SECRET:
        logger.warning(
            "[config] STRIPE_WEBHOOK_SECRET is unset while STRIPE_SECRET_KEY is set. "
            "Stripe webhooks will return 503 and Stripe will retry storm. "
            "Set STRIPE_WEBHOOK_SECRET via `fly secrets set ...` before taking payments."
        )
    if not SEMECLAW_API_KEY:
        logger.warning(
            "[config] SEMECLAW_API_KEY is unset. Write endpoints are unauthenticated. "
            "Set it for any non-demo deployment."
        )
    supa_url = os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip()
    supa_key = os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "").strip()
    if supa_url and not supa_key:
        logger.warning(
            "[config] DLS_TEAM_SUPABASE_URL set but DLS_TEAM_SUPABASE_SERVICE_KEY missing. "
            "Advertiser / AdClaw writes will fail."
        )


async def _start_background_tasks() -> list[asyncio.Task]:
    """Start long-lived background tasks for the dashboard process."""
    _startup_config_checks()
    tasks = [
        asyncio.create_task(file_watcher(), name="semeclaw-file-watcher"),
        asyncio.create_task(_droplet_probe_loop(), name="semeclaw-droplet-probe-loop"),
        asyncio.create_task(_prune_loop(), name="semeclaw-prune-loop"),
    ]
    if SEMECLAW_ADS_URL:
        tasks.append(asyncio.create_task(_register_with_adclaw(), name="semeclaw-adclaw-register"))
    return tasks


@asynccontextmanager
async def _lifespan(_: FastAPI):
    tasks = await _start_background_tasks()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="SemeClaw War Room Agent", version=APP_VERSION, lifespan=_lifespan)

# Serve static files (agents.html, images, etc.)
static_dir = Path(__file__).parent
if (static_dir / "agents.html").exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── /version — single source of truth for what's running on this machine ──
@app.get("/version")
async def _version():
    """Return the deployed build manifest. Written by scripts/deploy_and_sync.sh."""
    import json as _json
    manifest_path = static_dir / "version.json"
    runtime = {
        "app_version": APP_VERSION,
        "service": "semeclaw-war-room",
    }
    if manifest_path.exists():
        try:
            runtime.update(_json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            runtime["manifest_error"] = "could not parse version.json"
    else:
        runtime["manifest"] = "missing — run scripts/deploy_and_sync.sh to generate"
    return JSONResponse(runtime)

# ---------------------------------------------------------------------------
# Router mounts (incremental modularization of the monolithic server)
# Stubs are in war_room/dashboard/routes/ — real extraction is TODO.
# ---------------------------------------------------------------------------
try:
    from war_room.dashboard.routes import health, embed, agents, reports
    from war_room.dashboard.routes import meetings, webhooks, paperclip
    from war_room.dashboard.routes import billing, alerts, advertiser
    from war_room.dashboard.routes import tasks as tasks_routes
    from war_room.dashboard.routes import telegram as telegram_routes

    app.include_router(health.router)
    app.include_router(embed.router)
    app.include_router(agents.router)
    app.include_router(reports.router)
    app.include_router(meetings.router)
    app.include_router(webhooks.router)
    app.include_router(paperclip.router)
    app.include_router(billing.router)
    app.include_router(alerts.router)
    app.include_router(advertiser.router)
    app.include_router(tasks_routes.router)
    app.include_router(telegram_routes.router)
except Exception as e:
    logger.warning("Router mount skipped (routes may be stubs): %s", e)

# ---------------------------------------------------------------------------
# Standalone-agent hardening: CORS + optional bearer auth + CSP iframe
# ---------------------------------------------------------------------------
try:
    from fastapi.middleware.cors import CORSMiddleware
    _cors_origins = os.environ.get("SEMECLAW_CORS_ORIGINS", "*").strip()
    _allowed = ["*"] if _cors_origins == "*" else [o.strip() for o in _cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed,
        allow_credentials=True if _allowed != ["*"] else False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Speaker", "X-Voice", "X-TTS-Engine"],
    )
except Exception as _cors_err:
    logging.getLogger(__name__).warning("CORS setup failed: %s", _cors_err)

SEMECLAW_API_KEY = os.environ.get("SEMECLAW_API_KEY", "").strip()
SEMECLAW_FRAME_ANCESTORS = os.environ.get("SEMECLAW_FRAME_ANCESTORS", "*").strip()
SEMECLAW_TENANT_ID = os.environ.get("SEMECLAW_TENANT_ID", "default").strip()
SEMECLAW_PUBLIC_URL = os.environ.get("SEMECLAW_PUBLIC_URL", "http://127.0.0.1:8765").rstrip("/")

# Central AdClaw ad server — when set, loading slides are served from there
# and impressions are logged server-side (unfakeable even in open-source forks).
SEMECLAW_ADS_URL = os.environ.get("SEMECLAW_ADS_URL", "").rstrip("/")

# Persistent instance ID — auto-generated on first run, written to .instance_id
_INSTANCE_ID_FILE = WAR_ROOM_DIR / ".instance_id"


def _get_instance_id() -> str:
    if _INSTANCE_ID_FILE.exists():
        iid = _INSTANCE_ID_FILE.read_text().strip()
        if iid:
            return iid
    import secrets as _sec
    iid = _sec.token_urlsafe(24)
    try:
        _INSTANCE_ID_FILE.write_text(iid)
    except Exception:
        pass
    return iid


SEMECLAW_INSTANCE_ID: str = os.environ.get("SEMECLAW_INSTANCE_ID", "") or _get_instance_id()

# ---------------------------------------------------------------------------
# Subscription tier — free vs pro
# ---------------------------------------------------------------------------
import hmac as _hmac
import random as _random

# Comma-separated list of valid pro license keys, e.g. "key1,key2"
_PRO_KEYS: set[str] = {
    k.strip() for k in os.environ.get("SEMECLAW_PRO_KEYS", "").split(",") if k.strip()
}
# How many seconds free-tier users wait before the script is returned
FREE_TIER_WAIT_SECONDS: int = int(os.environ.get("SEMECLAW_FREE_WAIT_SECONDS", "8"))
# HMAC secret for signing watch tokens — auto-generated per process if not set
_SLIDES_SECRET: str = os.environ.get("SEMECLAW_SLIDES_SECRET", "")

LOADING_SLIDES_FILE = WAR_ROOM_DIR / "loading_slides.json"


def _get_tier(request: Request) -> str:
    """Return 'pro' if a valid license key is present, else 'free'."""
    if not _PRO_KEYS:
        return "free"
    key = (
        request.headers.get("x-semeclaw-license", "")
        or request.query_params.get("license", "")
    ).strip()
    return "pro" if key in _PRO_KEYS else "free"


def _get_slides_secret() -> bytes:
    """Return a stable HMAC secret for this process, initialised lazily."""
    global _SLIDES_SECRET
    if not _SLIDES_SECRET:
        import secrets as _sec
        _SLIDES_SECRET = _sec.token_hex(32)
    return _SLIDES_SECRET.encode()


def _issue_watch_token(ip: str) -> str:
    """Backward-compatible helper that now delegates to the verifiable v2 format."""
    return _issue_watch_token_v2(ip)


def _verify_watch_token(token: str, ip: str) -> bool:
    """Return True iff token was issued recently for this IP."""
    try:
        wall_str, sig = token.split(".", 1)
        wall = int(wall_str)
        # Reject tokens older than wait + 30s grace, or from the future
        age = _time.time() - wall
        if age < 0 or age > FREE_TIER_WAIT_SECONDS + 30:
            return False
        msg = f"{ip}:{wall}".encode()
        expected = _hmac.new(_get_slides_secret(), msg, "sha256").hexdigest()[:24]
        return _hmac.compare_digest(sig, expected)
    except Exception:
        return False


def _issue_watch_token_v2(ip: str) -> str:
    """Cleaner version: sign only (ip, wall) so we can verify without ts."""
    wall = int(_time.time())
    msg = f"{ip}:{wall}".encode()
    sig = _hmac.new(_get_slides_secret(), msg, "sha256").hexdigest()[:24]
    return f"{wall}.{sig}"


def _load_slides() -> list[dict]:
    try:
        return json.loads(LOADING_SLIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


import hashlib as _hashlib


def _hash_ip(ip: str) -> str:
    """One-way hash of IP for privacy-safe impression logging."""
    return _hashlib.sha256(ip.encode()).hexdigest()[:16]


async def _register_with_adclaw() -> None:
    """Register this War Room instance with the central AdClaw ad server on startup."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as _c:
            await _c.post(
                f"{SEMECLAW_ADS_URL}/api/instances/register",
                json={
                    "instance_id": SEMECLAW_INSTANCE_ID,
                    "tenant_id": SEMECLAW_TENANT_ID,
                    "public_url": SEMECLAW_PUBLIC_URL,
                },
            )
        logger.info("AdClaw: registered instance %s with %s", SEMECLAW_INSTANCE_ID, SEMECLAW_ADS_URL)
    except Exception as _e:
        logger.warning("AdClaw: instance registration failed (non-fatal): %s", _e)


# Write endpoints protected by bearer when SEMECLAW_API_KEY is set
_PROTECTED_WRITE_PATHS = (
    "/api/meeting/pin", "/api/meeting/unpin",
    "/api/meeting/finalize", "/api/meeting/replan",
    "/api/meeting/redirect",
    "/api/spotlight/impression",
    "/api/reports/delete",
    "/api/webhooks",
    "/api/reports",
    "/api/tasks",  # POST create/sync/gc, POST {id}/intervene/finalize/dialog
)


@app.middleware("http")
async def _semeclaw_auth_and_csp(request, call_next):
    # Bearer auth on protected write endpoints (POST/PUT/PATCH/DELETE only)
    _WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    if SEMECLAW_API_KEY and request.method in _WRITE_METHODS:
        path = request.url.path
        if any(path.startswith(p) for p in _PROTECTED_WRITE_PATHS):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {SEMECLAW_API_KEY}":
                from fastapi.responses import JSONResponse as _J
                return _J({"error": "unauthorized"}, status_code=401)
    response = await call_next(request)
    # Iframe-embed-friendly CSP
    if SEMECLAW_FRAME_ANCESTORS:
        response.headers["Content-Security-Policy"] = f"frame-ancestors {SEMECLAW_FRAME_ANCESTORS}"
    response.headers["X-SemeClaw-Version"] = APP_VERSION
    return response


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter (no external deps)
# Protects expensive public endpoints (TTS, audio) from abuse.
# Per-IP, per-path prefix. Limits reset after _RATE_LIMIT_WINDOW seconds.
# ---------------------------------------------------------------------------
import collections as _collections
import time as _time

_RATE_LIMIT_WINDOW = 60  # seconds

# Max requests per IP per window for each path prefix
_RATE_LIMIT_BY_PREFIX: dict[str, int] = {
    "/api/tts":             60,   # ElevenLabs (paid) + Kokoro (free) — generous
    "/api/stt":             30,   # Whisper — CPU heavy
    "/api/meeting/audio":  10,   # ffmpeg + TTS — heavy
    "/api/meeting/script": 30,   # pure compute, still throttled
}

# key: f"{ip}|{path_prefix}" → deque of monotonic timestamps
_RATE_WINDOWS: dict[str, _collections.deque] = {}


@app.middleware("http")
async def _rate_limiter(request: Request, call_next):
    path = request.url.path
    limit: int | None = None
    matched_prefix: str = ""
    for prefix, lim in _RATE_LIMIT_BY_PREFIX.items():
        if path.startswith(prefix):
            limit = lim
            matched_prefix = prefix
            break

    if limit is not None:
        ip = (request.client.host if request.client else "unknown")
        key = f"{ip}|{matched_prefix}"
        now = _time.monotonic()

        window = _RATE_WINDOWS.setdefault(key, _collections.deque())
        if len(_RATE_WINDOWS) > 5000:
            empty_keys = [k for k, v in _RATE_WINDOWS.items() if not v]
            for k in empty_keys:
                del _RATE_WINDOWS[k]
        cutoff = now - _RATE_LIMIT_WINDOW
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= limit:
            from fastapi.responses import JSONResponse as _J
            return _J(
                {"error": "rate_limit_exceeded", "retry_after": _RATE_LIMIT_WINDOW},
                status_code=429,
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        window.append(now)

    return await call_next(request)

# ---------------------------------------------------------------------------
# Paperclip — company ID cache
# ---------------------------------------------------------------------------
_paperclip_company_id: Optional[str] = None
PAPERCLIP_BASE = "http://127.0.0.1:3100"

async def _get_company_id() -> Optional[str]:
    global _paperclip_company_id
    if _paperclip_company_id:
        return _paperclip_company_id
    try:
        async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=5.0) as c:
            r = await c.get("/api/companies")
            r.raise_for_status()
            companies = r.json()
            if isinstance(companies, list) and companies:
                chosen = next((x for x in companies if "dans" in x.get("name", "").lower()), None)
                if not chosen:
                    chosen = max(companies, key=lambda x: x.get("issueCounter", 0))
                _paperclip_company_id = chosen["id"]
    except Exception as e:
        logger.warning("Paperclip company lookup failed: %s", e)
    return _paperclip_company_id

# ---------------------------------------------------------------------------
# Meeting context (shared cross-client, in-memory)
# ---------------------------------------------------------------------------
_meeting: list[dict] = []

# ---------------------------------------------------------------------------
# Task-driven meeting system (in-memory, no Supabase required)
# ---------------------------------------------------------------------------
_meeting_sessions: dict[str, dict] = {}           # meeting_id -> session
_meeting_waiters:  dict[str, asyncio.Event] = {}  # meeting_id -> event for user answer
_meeting_user_answers: dict[str, str] = {}        # meeting_id -> user answer text

# ---------------------------------------------------------------------------
# File watcher: polls for changes and broadcasts updates
# ---------------------------------------------------------------------------
_last_state_hash = ""
_last_log_count = 0
_last_report_count = 0

async def file_watcher():
    """Background task that polls for file changes and broadcasts updates."""
    global _last_state_hash, _last_log_count, _last_report_count
    while True:
        try:
            # Check state file
            if STATE_FILE.exists():
                content = STATE_FILE.read_text()
                state_hash = hash(content)
                if state_hash != _last_state_hash:
                    _last_state_hash = state_hash
                    await manager.broadcast({
                        "type": "state_update",
                        "state": json.loads(content),
                    })

            # Check for new log entries (use file size as proxy — avoids reading content every 3s)
            log_files = sorted(LOGS_DIR.glob("run-*.jsonl"), reverse=True)
            total_entries = sum(f.stat().st_size for f in log_files if f.exists())
            if total_entries != _last_log_count:
                _last_log_count = total_entries
                await manager.broadcast({"type": "new_log"})

            # Check for new reports
            report_count = len(list(RESEARCH_DIR.glob("*.md")))
            if report_count != _last_report_count:
                _last_report_count = report_count
                await manager.broadcast({"type": "new_report"})

        except Exception as e:
            logger.error("File watcher error: %s", e)

        await asyncio.sleep(3)  # Poll every 3 seconds

# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@app.get("/agents", response_class=HTMLResponse)
async def agents_page():
    """Serve the agents/orbital meeting room page."""
    agents_html = static_dir / "agents.html"
    if agents_html.exists():
        return HTMLResponse(content=agents_html.read_text(encoding="utf-8"))
    return JSONResponse({"error": "agents.html not found"}, status_code=404)


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page():
    """Serve the SemeClaw tasks UI (list + dialog + interventions)."""
    html_file = Path(__file__).parent / "tasks.html"
    if html_file.exists():
        return HTMLResponse(
            content=html_file.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )
    return HTMLResponse(content="<h1>tasks.html not found</h1>", status_code=404)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return HTMLResponse(
            content=html_file.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    return HTMLResponse(content="<h1>War Room Dashboard</h1><p>index.html not found.</p>")


@app.get("/api/state")
async def api_state():
    if STATE_FILE.exists():
        return JSONResponse(json.loads(STATE_FILE.read_text()))
    return JSONResponse({"error": "No state file", "metrics": {}, "completed_tasks": []})


@app.get("/api/stats")
async def api_stats():
    """Aggregated real-time dashboard stats for the metrics cards."""
    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    completed  = state.get("completed_tasks", [])
    active     = state.get("active_tasks", [])
    metrics    = state.get("metrics", {})
    today_str  = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for t in completed if (t.get("completed_at") or "").startswith(today_str))
    report_count = len(list(RESEARCH_DIR.glob("*.md")))
    return JSONResponse({
        "pipelines_total": len(completed),
        "pipelines_today": today_count,
        "active_now":      len(active),
        "reports":         report_count,
        "tasks_run":       metrics.get("tasks_run", 0),
        "tasks_succeeded": metrics.get("tasks_succeeded", 0),
        "tasks_failed":    metrics.get("tasks_failed", 0),
        "pc_issues":       metrics.get("paperclip_issues_created", 0),
    })


@app.get("/api/reports")
async def api_reports():
    _prune_old()  # enforce retention on listing
    files = []
    for d, saved in ((RESEARCH_SAVED, True), (RESEARCH_DIR, False)):
        for f in d.glob("*.md"):
            if not f.is_file():
                continue
            if d == RESEARCH_DIR and f.parent == RESEARCH_SAVED:
                continue  # skip dir-ception
            files.append((f, saved))
    files.sort(key=lambda t: t[0].stat().st_mtime, reverse=True)

    reports = []
    for f, saved in files[:40]:
        reports.append({
            "name":     f.name,
            "saved":    saved,
            "size":     f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "preview":  f.read_text(encoding="utf-8")[:300],
        })
    return JSONResponse(reports)


@app.get("/api/reports/content")
async def api_report_content(name: str):
    """Return the full markdown content of a report (checks saved/ first, then rolling)."""
    path = _find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        saved = path.parent == RESEARCH_SAVED
        return JSONResponse({"name": path.name, "saved": saved, "content": path.read_text(encoding="utf-8")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Ingest endpoints — external systems (NERVIX, Paperclip) create reports
# ---------------------------------------------------------------------------

import re as _re_ing, time as _time_ing, uuid as _uuid_ing, hashlib as _hash_ing, hmac as _hmac_ing


def _safe_report_name(raw: str, fallback_task: str = "task") -> str:
    """Turn any string into a safe .md filename."""
    stem = _re_ing.sub(r"[^a-zA-Z0-9_-]+", "-", (raw or fallback_task).strip())
    stem = stem.strip("-").lower()[:80] or "report"
    if not stem.endswith(".md"):
        stem += ".md"
    return stem


@app.post("/api/reports")
async def api_reports_create(request: Request):
    """Create a new report from JSON. Called by external systems (NERVIX,
    Paperclip adapters) when they want SemeClaw to convene a meeting.

    Body:
        {
          "name":  optional safe filename — auto-generated from `task` if missing
          "task":  one-line subject
          "content": full markdown body (agents as ## sections recommended)
          "auto_audio": bool — if true, build the MP3 now
          "tags": optional list
        }
    Response:
        {name, url, audio_url, saved: false}
    """
    data = await request.json()
    name    = (data.get("name") or "").strip()
    task    = (data.get("task") or "").strip()
    content = (data.get("content") or "").strip()

    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    if not name:
        # Auto-generate: "<slug>-YYYY-MM-DD.md"
        base = _safe_report_name(task).rstrip(".md")
        name = f"{base}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    name = Path(_safe_report_name(name)).name

    # Ensure well-formed header
    if not content.lstrip().startswith("#"):
        header = f"# War Room Report\n\n**Task:** {task or name}\n**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n**Via:** API\n\n---\n\n"
        content = header + content

    path = _report_dir_for_tenant(request) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    # Optional: generate the audio now
    audio_url = None
    if data.get("auto_audio"):
        mp3 = await _build_meeting_mp3(name)
        if mp3:
            audio_url = f"/api/meeting/audio?name={name}"

    url = f"/api/reports/content?name={name}"
    await _dispatch_webhook("report.created", {
        "name": name, "task": task, "url": url, "audio_url": audio_url,
        "tenant_id": _tenant_id(request),
    })
    return JSONResponse({
        "name": name, "saved": False,
        "url": url, "audio_url": audio_url,
        "tenant_id": _tenant_id(request),
    }, status_code=201)


@app.post("/api/reports/upload")
async def api_reports_upload(request: Request):
    """Multipart upload — drop a .md file directly.

    Form fields:
        file:  the .md file
        task:  optional subject (defaults to file stem)
        auto_audio: "true"/"false"
    """
    from fastapi import File, UploadFile, Form  # noqa: F401
    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)
    raw = await f.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"error": "file must be utf-8 text"}, status_code=400)
    task = (form.get("task") or Path(f.filename or "").stem or "task")
    name = _safe_report_name(Path(f.filename or "").stem or task)

    path = _report_dir_for_tenant(request) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    audio_url = None
    if (form.get("auto_audio") or "").lower() in ("1", "true", "yes"):
        mp3 = await _build_meeting_mp3(name)
        if mp3:
            audio_url = f"/api/meeting/audio?name={name}"

    await _dispatch_webhook("report.created", {
        "name": name, "task": task, "via": "upload",
        "url": f"/api/reports/content?name={name}", "audio_url": audio_url,
        "tenant_id": _tenant_id(request),
    })
    return JSONResponse({
        "name": name, "saved": False,
        "url": f"/api/reports/content?name={name}",
        "audio_url": audio_url,
        "tenant_id": _tenant_id(request),
    }, status_code=201)


@app.delete("/api/reports")
async def api_reports_delete(name: str):
    """Delete a report and its cached meeting audio (if any)."""
    path = _find_report(name)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        path.unlink()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Also remove any cached meeting MP3 whose stem matches this report
    removed_audio = 0
    for d in (MEETINGS_DIR, MEETINGS_SAVED):
        for f in d.glob("*.mp3"):
            if path.stem in f.stem:
                try:
                    f.unlink()
                    removed_audio += 1
                except Exception:
                    pass
    await _dispatch_webhook("report.deleted", {"name": name})
    return JSONResponse({"ok": True, "deleted": name, "audio_files_removed": removed_audio})


# ---------------------------------------------------------------------------
# Tenant isolation — honours X-Tenant-Id header (falls back to env default)
# ---------------------------------------------------------------------------

def _tenant_id(request: Request) -> str:
    """Resolve tenant id. Header wins, then env default."""
    t = (request.headers.get("x-tenant-id") or "").strip()
    return t or SEMECLAW_TENANT_ID or "default"


def _report_dir_for_tenant(request: Request) -> Path:
    """Return the research dir, scoped to tenant when non-default."""
    t = _tenant_id(request)
    if t == "default":
        return RESEARCH_DIR
    base = WAR_ROOM_DIR / "tenants" / _re_ing.sub(r"[^a-zA-Z0-9_-]", "-", t) / "research"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Webhooks — register URL + receive lifecycle events
# ---------------------------------------------------------------------------

WEBHOOKS_FILE = WAR_ROOM_DIR / "webhooks.json"


def _load_webhooks() -> list[dict]:
    if not WEBHOOKS_FILE.exists():
        return []
    try:
        return json.loads(WEBHOOKS_FILE.read_text())
    except Exception:
        return []


def _save_webhooks(hooks: list[dict]) -> None:
    WEBHOOKS_FILE.write_text(json.dumps(hooks, indent=2))


# In-process event bus — SSE subscribers receive every lifecycle event
_SSE_SUBSCRIBERS: set[asyncio.Queue] = set()


async def _dispatch_webhook(event: str, payload: dict) -> None:
    """Fire-and-forget broadcast to SSE subscribers AND registered webhooks."""
    body = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent_version": APP_VERSION,
        "tenant_id": payload.get("tenant_id", SEMECLAW_TENANT_ID),
        "data": payload,
    }
    _bump("webhooks_fired")

    # 1. Fan out to live SSE subscribers (local dashboards, NERVIX UI, Paperclip)
    dead: list[asyncio.Queue] = []
    for q in _SSE_SUBSCRIBERS:
        try:
            q.put_nowait(body)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _SSE_SUBSCRIBERS.discard(q)

    # 2. Fan out to HTTP webhooks (async, signed)
    hooks = _load_webhooks()
    matches = [h for h in hooks if event in (h.get("events") or []) or "*" in (h.get("events") or [])]
    if not matches:
        return
    raw = json.dumps(body).encode("utf-8")
    for h in matches:
        try:
            secret = (h.get("secret") or "").encode("utf-8")
            sig = _hmac_ing.new(secret, raw, _hash_ing.sha256).hexdigest() if secret else ""
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    h["url"],
                    content=raw,
                    headers={
                        "content-type": "application/json",
                        "x-semeclaw-event": event,
                        "x-semeclaw-signature": f"sha256={sig}" if sig else "",
                        "user-agent": f"SemeClaw/{APP_VERSION}",
                    },
                )
        except Exception as e:
            logger.warning("webhook %s → %s failed: %s", event, h.get("url"), e)


@app.get("/api/events")
async def api_events(tenant: str | None = None, events: str | None = None):
    """Server-Sent Events stream of lifecycle events.

    Query params:
        tenant=<id>      only forward events for this tenant
        events=a,b,c     filter to these event names (comma-sep)

    Usage (browser):
        const es = new EventSource('/api/events');
        es.addEventListener('meeting.finalized', e => console.log(JSON.parse(e.data)));

    Usage (server/Python):
        httpx.stream('GET', url) then iter_lines() — parse SSE frames manually.
    """
    from fastapi.responses import StreamingResponse
    wanted_events = set()
    if events:
        wanted_events = {e.strip() for e in events.split(",") if e.strip()}

    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _SSE_SUBSCRIBERS.add(q)

    async def _stream():
        # Initial hello
        hello = {"event": "connected", "ts": datetime.now(timezone.utc).isoformat(),
                 "agent_version": APP_VERSION, "data": {"subscribers": len(_SSE_SUBSCRIBERS)}}
        yield f"event: connected\ndata: {json.dumps(hello)}\n\n"
        try:
            while True:
                try:
                    body = await asyncio.wait_for(q.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    # Keepalive comment — stops intermediaries from closing the stream
                    yield ": keepalive\n\n"
                    continue
                ev = body.get("event", "message")
                if wanted_events and ev not in wanted_events:
                    continue
                if tenant and body.get("tenant_id") != tenant:
                    continue
                yield f"event: {ev}\ndata: {json.dumps(body)}\n\n"
        finally:
            _SSE_SUBSCRIBERS.discard(q)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@app.post("/api/webhooks")
async def api_webhooks_register(request: Request):
    """Register a webhook URL for lifecycle events.
    Body: {url, events: ["meeting.finalized", ...], secret?}"""
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url or not url.startswith("http"):
        return JSONResponse({"error": "valid url required"}, status_code=400)
    events = data.get("events") or ["*"]
    secret = data.get("secret") or ""
    hooks = _load_webhooks()
    hook_id = str(_uuid_ing.uuid4())[:8]
    hooks.append({"id": hook_id, "url": url, "events": events, "secret": secret,
                  "created": datetime.now(timezone.utc).isoformat()})
    _save_webhooks(hooks)
    return JSONResponse({"ok": True, "id": hook_id, "url": url, "events": events})


@app.get("/api/webhooks")
async def api_webhooks_list():
    hooks = _load_webhooks()
    # Redact secrets
    return JSONResponse([{**h, "secret": "***" if h.get("secret") else ""} for h in hooks])


@app.delete("/api/webhooks/{hook_id}")
async def api_webhooks_delete(hook_id: str):
    hooks = _load_webhooks()
    before = len(hooks)
    hooks = [h for h in hooks if h.get("id") != hook_id]
    if len(hooks) == before:
        return JSONResponse({"error": "not found"}, status_code=404)
    _save_webhooks(hooks)
    return JSONResponse({"ok": True, "deleted": hook_id})


# ---------------------------------------------------------------------------
# Share links — public playback URLs without bearer auth
# ---------------------------------------------------------------------------

SHARES_FILE = WAR_ROOM_DIR / "shares.json"
SHARE_TTL_DAYS = 30


def _load_shares() -> dict:
    if not SHARES_FILE.exists():
        return {}
    try:
        return json.loads(SHARES_FILE.read_text())
    except Exception:
        return {}


def _save_shares(shares: dict) -> None:
    SHARES_FILE.write_text(json.dumps(shares, indent=2))


@app.post("/api/meetings/{name}/share")
async def api_meeting_share(name: str):
    """Create a share token for a meeting. Returns public URL good for SHARE_TTL_DAYS days."""
    safe = Path(name).name
    if not _find_report(safe):
        return JSONResponse({"error": "report not found"}, status_code=404)
    token = _uuid_ing.uuid4().hex[:16]
    expires = int(_time_ing.time()) + SHARE_TTL_DAYS * 86400
    shares = _load_shares()
    shares[token] = {"name": safe, "expires": expires,
                     "created": datetime.now(timezone.utc).isoformat()}
    _save_shares(shares)
    return JSONResponse({
        "ok": True,
        "token": token,
        "url": f"{SEMECLAW_PUBLIC_URL}/share/{token}",
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
    })


@app.get("/share/{token}")
async def share_page(token: str):
    """Public landing page for a shared meeting — serves the embed UI without auth."""
    from fastapi.responses import FileResponse
    shares = _load_shares()
    s = shares.get(token)
    if not s or s.get("expires", 0) < _time_ing.time():
        return JSONResponse({"error": "share link invalid or expired"}, status_code=410)
    index = Path(__file__).parent / "index.html"
    return FileResponse(index, media_type="text/html",
                        headers={"X-SemeClaw-Share": token,
                                 "X-SemeClaw-Meeting": s["name"]})


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_METRICS = {
    "meetings_started":   0,
    "meetings_finalized": 0,
    "questions_asked":    0,
    "tts_requests":       0,
    "reports_created":    0,
    "webhooks_fired":     0,
}


def _bump(key: str, n: int = 1) -> None:
    _METRICS[key] = _METRICS.get(key, 0) + n


# ---------------------------------------------------------------------------
# v0.5.0 — Voice overrides, Meeting templates, Cost ledger, Audit
# ---------------------------------------------------------------------------

VOICE_MAP_FILE = WAR_ROOM_DIR / "voice_overrides.json"


def _load_voice_overrides() -> dict:
    if not VOICE_MAP_FILE.exists():
        return {}
    try:
        return json.loads(VOICE_MAP_FILE.read_text())
    except Exception:
        return {}


def _save_voice_overrides(d: dict) -> None:
    VOICE_MAP_FILE.write_text(json.dumps(d, indent=2))


def _resolve_voice_for_tenant(speaker: str, tenant_id: str) -> str:
    """Return the overridden voice for (tenant, speaker) if set, else the default."""
    overrides = _load_voice_overrides()
    tenant_map = overrides.get(tenant_id, {})
    if speaker in tenant_map:
        return tenant_map[speaker]
    # Fallback to global overrides ("default" tenant) then to _ELEVEN_VOICES
    global_map = overrides.get("default", {})
    return global_map.get(speaker, _ELEVEN_VOICES.get(speaker, ""))


@app.get("/api/voices/map")
async def api_voices_map(request: Request):
    """Current {speaker → voice} mapping for this tenant. Shows defaults
    overlaid with any custom overrides."""
    tenant = _tenant_id(request)
    overrides = _load_voice_overrides().get(tenant, {})
    merged = {**_ELEVEN_VOICES, **overrides}
    return JSONResponse({
        "tenant_id": tenant,
        "defaults": _ELEVEN_VOICES,
        "overrides": overrides,
        "effective": merged,
    })


@app.put("/api/voices/map")
async def api_voices_map_set(request: Request):
    """Update the voice map for this tenant.
    Body: {"speaker_name": "voice_name", ...}
    Unknown speakers are accepted. To clear a mapping, set value to null."""
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tenant = _tenant_id(request)
    overrides = _load_voice_overrides()
    current = overrides.get(tenant, {})
    for speaker, voice in data.items():
        if voice in (None, ""):
            current.pop(speaker, None)
        else:
            current[speaker] = str(voice)
    overrides[tenant] = current
    _save_voice_overrides(overrides)
    return JSONResponse({
        "ok": True, "tenant_id": tenant,
        "overrides": current,
    })


# --- Meeting templates ---------------------------------------------------
MEETING_TEMPLATES = {
    "bug-triage": {
        "id": "bug-triage",
        "name": "Bug Triage",
        "description": "Team reviews a bug report, assigns severity, owner, and next step.",
        "icon": "🐛",
        "markdown_template": (
            "# Bug Triage — {title}\n\n"
            "**Task:** {title}\n\n"
            "## Research Agent\n\n"
            "Repro steps: {repro}\nImpact: {impact}\nFirst seen: {first_seen}.\n\n"
            "## Strategist Agent\n\n"
            "Severity recommendation + priority tier. Cross-reference known similar incidents.\n\n"
            "## Writer Agent\n\n"
            "Write the fix plan: 1-2 sentence approach, assign owner, estimate LOE.\n"
        ),
        "required_fields": ["title", "repro", "impact", "first_seen"],
    },
    "sprint-planning": {
        "id": "sprint-planning",
        "name": "Sprint Planning",
        "description": "Team scopes upcoming sprint by balancing priorities against capacity.",
        "icon": "🏃",
        "markdown_template": (
            "# Sprint Planning — {sprint_name}\n\n"
            "**Task:** Plan sprint {sprint_name}\n"
            "**Capacity:** {capacity} story points\n\n"
            "## Research Agent\n\n"
            "Pull backlog stats: open items, recent velocity, carry-over.\n\n"
            "## Strategist Agent\n\n"
            "Top priorities: {priorities}. Trade-off matrix vs available capacity.\n\n"
            "## Writer Agent\n\n"
            "Commit list: ordered stories with point estimates summing to ≤ capacity.\n"
        ),
        "required_fields": ["sprint_name", "capacity", "priorities"],
    },
    "post-mortem": {
        "id": "post-mortem",
        "name": "Post-Mortem",
        "description": "Blameless incident review with clear corrective actions.",
        "icon": "🔥",
        "markdown_template": (
            "# Post-Mortem — {incident}\n\n"
            "**Task:** Post-mortem for {incident}\n"
            "**Duration:** {duration}\n"
            "**Impact:** {impact}\n\n"
            "## Research Agent\n\n"
            "Timeline of events leading up to and during the incident.\n\n"
            "## Strategist Agent\n\n"
            "Root cause analysis (5 whys). Was this a known risk?\n\n"
            "## Writer Agent\n\n"
            "3 concrete action items with owners + due dates. Preventive + detective.\n"
        ),
        "required_fields": ["incident", "duration", "impact"],
    },
    "customer-interview": {
        "id": "customer-interview",
        "name": "Customer Interview Debrief",
        "description": "Synthesize a customer call into insights + next steps.",
        "icon": "💬",
        "markdown_template": (
            "# Customer Interview — {customer}\n\n"
            "**Task:** Debrief {customer} interview\n"
            "**Persona:** {persona}\n\n"
            "## Research Agent\n\n"
            "Key quotes + raw observations from the call.\n\n"
            "## Strategist Agent\n\n"
            "Pattern match against existing customer data. What's a signal vs noise?\n\n"
            "## Writer Agent\n\n"
            "Top 3 product implications + recommended follow-up.\n"
        ),
        "required_fields": ["customer", "persona"],
    },
}


@app.get("/api/meeting/templates")
async def api_meeting_templates():
    """List available meeting templates."""
    return JSONResponse({
        "templates": list(MEETING_TEMPLATES.values()),
        "count": len(MEETING_TEMPLATES),
    })


@app.get("/api/meeting/templates/{template_id}")
async def api_meeting_template_get(template_id: str):
    tpl = MEETING_TEMPLATES.get(template_id)
    if not tpl:
        return JSONResponse({"error": "template not found"}, status_code=404)
    return JSONResponse(tpl)


@app.post("/api/meeting/templates/{template_id}/use")
async def api_meeting_template_use(template_id: str, request: Request):
    """Convene a meeting from a template.
    Body: {fields: {...}, auto_audio?: bool, tenant_id?}
    Returns the same shape as /api/paperclip/trigger."""
    tpl = MEETING_TEMPLATES.get(template_id)
    if not tpl:
        return JSONResponse({"error": "template not found"}, status_code=404)

    body = await request.json()
    fields = body.get("fields") or {}
    required = tpl.get("required_fields") or []
    missing = [f for f in required if not fields.get(f)]
    if missing:
        return JSONResponse({
            "error": f"missing required fields: {', '.join(missing)}",
            "required": required,
        }, status_code=400)

    try:
        markdown = tpl["markdown_template"].format(**fields)
    except KeyError as e:
        return JSONResponse({"error": f"missing template field: {e}"}, status_code=400)

    task = fields.get("title") or fields.get("incident") or fields.get("customer") or fields.get("sprint_name") or tpl["name"]
    name = f"{template_id}-{_re_ing.sub(r'[^a-z0-9]+', '-', task.lower()).strip('-')[:50]}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"

    base = _report_dir_for_tenant(request)
    path = base / name
    path.write_text(markdown, encoding="utf-8")

    audio_url = None
    if body.get("auto_audio"):
        mp3 = await _build_meeting_mp3(name)
        if mp3:
            audio_url = f"{SEMECLAW_PUBLIC_URL}/api/meeting/audio?name={name}"

    await _dispatch_webhook("template.used", {
        "template_id": template_id, "report_name": name,
        "tenant_id": _tenant_id(request),
    })

    return JSONResponse({
        "ok": True,
        "template_id": template_id,
        "report_name": name,
        "audio_url": audio_url,
        "embed_url": f"{SEMECLAW_PUBLIC_URL}/embed?meeting={name}&v=2",
        "script_url": f"{SEMECLAW_PUBLIC_URL}/api/meeting/script?name={name}",
        "tenant_id": _tenant_id(request),
    })


# --- Cost ledger ---------------------------------------------------------
_COST_LEDGER: dict[str, dict[str, int]] = {}  # tenant_id → {metric: count}


def _cost_bump(tenant: str, metric: str, n: int = 1) -> None:
    bucket = _COST_LEDGER.setdefault(tenant, {})
    bucket[metric] = bucket.get(metric, 0) + n


@app.get("/api/tenants/{tenant_id}/costs")
async def api_tenant_costs(tenant_id: str):
    """Per-tenant usage counters. Useful for metered billing upstream."""
    usage = _COST_LEDGER.get(tenant_id, {})
    # Approximate USD cost hints (adjust to your pricing):
    #   ElevenLabs Flash v2.5  ≈ $5 per 1M chars
    #   Gemini 2.5 Flash       ≈ $0.30 per 1M input tokens
    tts_chars = usage.get("tts_chars", 0)
    llm_tokens = usage.get("llm_tokens", 0)
    approx_cents = int((tts_chars / 1_000_000 * 500) + (llm_tokens / 1_000_000 * 30))
    return JSONResponse({
        "tenant_id": tenant_id,
        "usage": usage,
        "approx_cost_cents": approx_cents,
        "pricing_notes": {
            "tts_chars_per_dollar":   200_000,
            "llm_tokens_per_dollar":  3_333_333,
        },
    })


@app.get("/api/tenants/costs")
async def api_tenants_costs_all():
    """List cost snapshots for every known tenant."""
    out = []
    for t, usage in _COST_LEDGER.items():
        tts_chars = usage.get("tts_chars", 0)
        llm_tokens = usage.get("llm_tokens", 0)
        out.append({
            "tenant_id": t,
            "usage": usage,
            "approx_cost_cents": int((tts_chars / 1_000_000 * 500) + (llm_tokens / 1_000_000 * 30)),
        })
    return JSONResponse(sorted(out, key=lambda x: x["approx_cost_cents"], reverse=True))


# ---------------------------------------------------------------------------
# v0.6.0 — Voice cloning, SRT/PDF exports, Stripe billing hook
# ---------------------------------------------------------------------------

@app.post("/api/voices/clone")
async def api_voices_clone(request: Request):
    """Clone a voice via ElevenLabs Instant Voice Clone and register it for
    this tenant. The new voice_id is immediately usable in /api/tts for the
    speaker mapping the consumer chooses via /api/voices/map.

    Multipart form fields:
        file        — reference audio (.mp3, .wav), 30s-2min recommended
        name        — display name for the clone (e.g. 'Dan Primary')
        description — optional descriptor
        speaker     — optional; if set, auto-registers mapping for tenant
    """
    tenant = _tenant_id(request)
    if not _ELEVEN_KEY:
        return JSONResponse({"error": "ELEVENLABS_API_KEY not configured"}, status_code=503)

    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)
    name = (form.get("name") or f.filename or "Cloned Voice").strip()
    description = (form.get("description") or "").strip()
    speaker_map_key = (form.get("speaker") or "").strip()

    # Stream to ElevenLabs IVC endpoint
    try:
        import httpx as _httpx
        audio_bytes = await f.read()
        files = {"files": (f.filename or "sample.mp3", audio_bytes, "audio/mpeg")}
        data = {"name": name, "description": description}
        async with _httpx.AsyncClient(timeout=60.0) as c:
            resp = await c.post(
                "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": _ELEVEN_KEY, "accept": "application/json"},
                files=files,
                data=data,
            )
        if resp.status_code != 200:
            return JSONResponse({"error": f"ElevenLabs {resp.status_code}: {resp.text[:300]}"}, status_code=502)
        voice = resp.json()
    except Exception as e:
        logger.warning(f"voice clone failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    voice_id = voice.get("voice_id") or voice.get("voice_id_")
    if not voice_id:
        return JSONResponse({"error": "no voice_id returned", "upstream": voice}, status_code=502)

    # Cache it in our in-process voice_id map so /api/tts can use it by name
    _ELEVEN_VOICE_ID_CACHE[name] = voice_id

    # Optionally bind to a speaker mapping for this tenant
    if speaker_map_key:
        overrides = _load_voice_overrides()
        current = overrides.get(tenant, {})
        current[speaker_map_key] = name
        overrides[tenant] = current
        _save_voice_overrides(overrides)

    await _dispatch_webhook("voice.cloned", {
        "voice_id": voice_id, "name": name,
        "tenant_id": tenant, "speaker_bound": speaker_map_key or None,
    })

    return JSONResponse({
        "ok": True,
        "voice_id": voice_id,
        "name": name,
        "tenant_id": tenant,
        "bound_to_speaker": speaker_map_key or None,
    })


@app.get("/api/meetings/{name}/transcript.srt")
async def api_meeting_srt(name: str):
    """Generate SRT subtitles from a meeting script. Approximates timing
    at ~150 WPM plus a fixed pause — good enough for YouTube-style overlays."""
    from fastapi.responses import Response as FR
    from meeting_skill import build_script

    path = _find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "not found"}, status_code=404)
    content = path.read_text(encoding="utf-8")
    task = _extract_task_from_report(content)
    meeting_id = _lookup_run_id_for_task(task) or path.stem
    script = build_script(report_content=content, task=task, meeting_id=meeting_id)

    def _fmt(ms: int) -> str:
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms2 = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms2:03d}"

    WPM = 150
    lines: list[str] = []
    t_ms = 0
    for i, seg in enumerate(script.segments, 1):
        words = max(1, len(seg.text.split()))
        dur_ms = int((words / WPM) * 60_000)
        start = t_ms
        end = t_ms + dur_ms
        lines.append(str(i))
        lines.append(f"{_fmt(start)} --> {_fmt(end)}")
        lines.append(f"{seg.speaker}: {seg.text}")
        lines.append("")
        t_ms = end + int(seg.pause_ms_after or 0)

    body = "\n".join(lines)
    return FR(
        content=body,
        media_type="application/x-subrip",
        headers={
            "Content-Disposition": f'attachment; filename="{path.stem}.srt"',
        },
    )


@app.get("/api/meetings/{name}/transcript.html")
async def api_meeting_html(name: str):
    """Render a nicely styled standalone HTML transcript for the meeting.
    Zero extra deps; consumers can print-to-PDF in the browser or pipe to
    chrome --headless --print-to-pdf for a real PDF."""
    from fastapi.responses import HTMLResponse
    from meeting_skill import build_script

    path = _find_report(name)
    if not path or path.suffix != ".md":
        return HTMLResponse(f"<h1>404</h1><p>Report not found: {name}</p>", status_code=404)
    content = path.read_text(encoding="utf-8")
    task = _extract_task_from_report(content)
    meeting_id = _lookup_run_id_for_task(task) or path.stem
    script = build_script(report_content=content, task=task, meeting_id=meeting_id)

    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    turns_html = []
    color_map = {
        "Narrator": "#8b5cf6", "David": "#f59e0b", "Dan": "#10b981",
        "Autoresearch": "#06b6d4", "GSD": "#ec4899", "Hermes": "#eab308",
    }
    for seg in script.segments:
        col = color_map.get(seg.speaker, "#64748b")
        turns_html.append(
            f'<div class="turn role-{seg.role or "agent"}">'
            f'<div class="speaker" style="color:{col}">{_esc(seg.speaker)}</div>'
            f'<div class="text">{_esc(seg.text)}</div>'
            f'</div>'
        )
    qa_note = " · including any Q&A interjections" if "## 💬 Meeting Interjections" in content else ""
    verdict = ""
    for line in content.splitlines()[::-1]:
        if line.strip().startswith("VERDICT:"):
            verdict = line.strip()
            break

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Meeting Transcript — {_esc(script.subject)}</title>
<style>
  :root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;--amber:#f59e0b}}
  @media print{{:root{{--bg:#fff}}}}
  *{{box-sizing:border-box}}
  body{{margin:0;padding:40px 28px;background:var(--bg);color:var(--text);font-family:-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.6;max-width:780px;margin:0 auto}}
  header{{border-bottom:3px solid var(--amber);padding-bottom:18px;margin-bottom:28px}}
  h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.4px}}
  .meta{{color:var(--muted);font-size:13px;font-family:ui-monospace,monospace}}
  .meta span{{margin-right:14px}}
  .verdict{{display:inline-block;margin-top:14px;padding:6px 14px;background:#10b98122;border:1px solid #10b98155;color:#047857;border-radius:6px;font-family:ui-monospace,monospace;font-size:12px;font-weight:700}}
  .turn{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:10px;break-inside:avoid}}
  .turn.role-host{{background:#8b5cf608;border-color:#8b5cf633}}
  .turn.role-orchestrator{{background:#f59e0b08;border-color:#f59e0b33}}
  .turn.role-dan{{background:#10b98108;border-color:#10b98133}}
  .speaker{{font-weight:700;font-size:11px;letter-spacing:.8px;text-transform:uppercase;margin-bottom:6px}}
  .text{{font-size:15px}}
  footer{{margin-top:40px;padding-top:18px;border-top:1px solid var(--border);color:var(--muted);font-size:11px;font-family:ui-monospace,monospace;text-align:center}}
</style></head>
<body>
  <header>
    <h1>{_esc(script.subject)}</h1>
    <div class="meta">
      <span>🎭 Meeting <b>{_esc(script.meeting_id)}</b></span>
      <span>👥 {len(script.attendees)} attendees{qa_note}</span>
      <span>📝 {len(script.segments)} turns</span>
    </div>
    {f'<div class="verdict">{_esc(verdict)}</div>' if verdict else ''}
  </header>
  {''.join(turns_html)}
  <footer>
    Generated by SemeClaw War Room v{request_version()} · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
  </footer>
</body></html>"""

    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'inline; filename="{path.stem}.html"'},
    )


def request_version() -> str:
    """Used by the HTML transcript footer. Keeps string in one place."""
    return APP_VERSION


# ---------------------------------------------------------------------------
# Stripe billing scaffold — wires up but stays inert until STRIPE_SECRET_KEY is set
# ---------------------------------------------------------------------------

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_PER_MEETING = os.environ.get("STRIPE_PRICE_PER_MEETING", "").strip()  # price_xxx


# MOVED TO routes/billing.py
# @app.get("/api/billing/status")
# async def api_billing_status(request: Request):
#     """Report billing configuration + current tenant usage. Lets consumers
#     surface 'usage this month: $X' in their own UI even before Stripe is wired."""
#     tenant = _tenant_id(request)
#     usage = _COST_LEDGER.get(tenant, {})
#     configured = bool(STRIPE_SECRET_KEY and STRIPE_PRICE_PER_MEETING)
#     return JSONResponse({
#         "tenant_id": tenant,
#         "stripe_configured": configured,
#         "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY or None,
#         "usage": usage,
#         "suggested_pricing": {
#             "model": "per_meeting",
#             "est_cents_per_meeting": 25,
#             "included_per_tier": {"trial": 10, "pro": 100, "team": 500},
#         },
#     })
#
#
# @app.post("/api/billing/report-usage")
# async def api_billing_report_usage(request: Request):
#     """Push this tenant's meeting count to Stripe as a metered subscription
#     usage record. Returns 503 until Stripe is configured — scaffold only."""
#     tenant = _tenant_id(request)
#     if not (STRIPE_SECRET_KEY and STRIPE_PRICE_PER_MEETING):
#         return JSONResponse({
#             "error": "stripe not configured",
#             "required_env": ["STRIPE_SECRET_KEY", "STRIPE_PRICE_PER_MEETING"],
#             "tenant_id": tenant,
#         }, status_code=503)
#     # Real impl would POST to Stripe subscription_item/{id}/usage_records
#     # using the tenant's stored subscription_item_id. Kept as scaffold —
#     # consumers should extend with their tenant ↔ subscription mapping.
#     return JSONResponse({"ok": True, "scaffold_only": True, "tenant_id": tenant})
#
#
# @app.get("/metrics")
# async def metrics():
#     """Prometheus exposition format. Scrape with Prometheus, Grafana Agent, etc."""
#     from fastapi.responses import Response as FR
#     lines = [
#         "# HELP semeclaw_info SemeClaw agent info",
#         "# TYPE semeclaw_info gauge",
#         f'semeclaw_info{{version="{APP_VERSION}",tenant="{SEMECLAW_TENANT_ID}"}} 1',
#     ]
#     for k, v in _METRICS.items():
#         lines.append(f"# HELP semeclaw_{k} Count of {k}")
#         lines.append(f"# TYPE semeclaw_{k} counter")
#         lines.append(f"semeclaw_{k}_total {v}")
#     return FR("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
#
#
def _extract_task_from_report(content: str) -> str:
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("**Task:**"):
            return s.removeprefix("**Task:**").strip()
        if s.lower().startswith("# ") and "report" not in s.lower():
            return s[2:].strip()
    return "Fleet review"


def _lookup_run_id_for_task(task: str) -> str:
    """Find the most recent run_id whose task matches this report, if any."""
    if not task:
        return ""
    try:
        for log_file in sorted(LOGS_DIR.glob("run-*.jsonl"), reverse=True)[:5]:
            for line in reversed(log_file.read_text(encoding="utf-8").splitlines()):
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("task", "").strip().lower() == task.strip().lower():
                    return entry.get("run_id", "")
    except Exception:
        pass
    return ""


SCRIPTS_CACHE_DIR = WAR_ROOM_DIR / "audio" / "scripts"
SCRIPTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Task-meeting index — persists pre-generated meeting scripts per task
# ---------------------------------------------------------------------------

TASK_MEETINGS_FILE = WAR_ROOM_DIR / "task_meetings.json"


def _store_task_meeting(task: str, run_id: str, report_name: str, script: dict):
    """Store meeting script indexed by task slug. Cap at 100 entries (newest first)."""
    try:
        data = json.loads(TASK_MEETINGS_FILE.read_text(encoding="utf-8")) if TASK_MEETINGS_FILE.exists() else {}
        key = re.sub(r"[^a-z0-9]+", "-", task.lower())[:60]
        data[key] = {
            "task": task,
            "run_id": run_id,
            "report_name": report_name,
            "segments": script.get("segments", []),
            "title": script.get("title", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Keep latest 100
        if len(data) > 100:
            sorted_keys = sorted(data, key=lambda k: data[k].get("created_at", ""), reverse=True)
            data = {k: data[k] for k in sorted_keys[:100]}
        TASK_MEETINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("_store_task_meeting error: %s", e)


def _get_task_meeting(task: str) -> dict | None:
    """Retrieve cached meeting script for a task slug."""
    if not TASK_MEETINGS_FILE.exists():
        return None
    try:
        key = re.sub(r"[^a-z0-9]+", "-", task.lower())[:60]
        data = json.loads(TASK_MEETINGS_FILE.read_text(encoding="utf-8"))
        return data.get(key)
    except Exception:
        return None

_LANG_FULL_NAMES = {
    "en": "English", "ro": "Romanian", "de": "German", "fr": "French",
    "es": "Spanish", "pt": "Portuguese", "it": "Italian", "zh": "Simplified Chinese",
    "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "ru": "Russian",
}


async def _translate_script(segments: list[dict], lang: str) -> list[dict]:
    """Translate each segment individually (reliable order preservation)."""
    target = _LANG_FULL_NAMES.get(lang, lang)
    system = (
        f"You are a professional meeting interpreter. Translate the following line into natural, "
        f"spoken {target}. Preserve tone and intent. Keep product/person names (NERVIX, David, Dan, "
        f"GSD, Hermes, Autoresearch, Narrator) exactly as-is. Output ONLY the translated line — "
        f"no commentary, no quotes, no prefix, no numbering."
    )

    # Concurrent translation with bounded parallelism
    sem = asyncio.Semaphore(4)

    async def _one(seg: dict) -> dict:
        async with sem:
            try:
                tr = await _call_openrouter("google/gemini-2.5-flash", system, seg["text"])
            except Exception:
                tr = None
        text = (tr or "").strip().strip('"').strip("'") or seg["text"]
        return {**seg, "text": text}

    return await asyncio.gather(*[_one(s) for s in segments])


@app.post("/api/meeting/redirect")
async def api_meeting_redirect(request: Request):
    """User injects a question/command mid-meeting. Pick best agent + generate response."""
    _bump("questions_asked")
    data = await request.json()
    question = (data.get("question") or "").strip()
    attendees = data.get("attendees") or []
    history = data.get("history") or []  # list of {speaker,text}
    subject = (data.get("subject") or "").strip()
    if not question:
        return JSONResponse({"error": "no question"}, status_code=400)

    # Compose a short context for the LLM
    attendees_str = ", ".join(a for a in attendees if a and a not in ("Narrator", "Dan"))
    history_str = "\n".join(f"{h.get('speaker','?')}: {h.get('text','')[:300]}" for h in history[-8:])
    system = (
        "You are the orchestrator of a live war-room meeting. Dan just interjected with a "
        "question. Pick the single best agent from the attendees to answer. Return STRICT JSON "
        'with this shape: {"responder":"<AgentName>","response":"<one or two sentences>"}. '
        "The response must stay in character for that agent, be concise (<=2 sentences), and "
        "directly address Dan's question."
    )
    user = (
        f"Meeting subject: {subject}\n"
        f"Attendees (pick one): {attendees_str}\n"
        f"Recent transcript:\n{history_str}\n\n"
        f"Dan's interjection: {question}\n\n"
        'Return only JSON. Example: {"responder":"GSD","response":"Short answer."}'
    )
    raw = await _call_openrouter("google/gemini-2.5-flash", system, user)
    if not raw:
        return JSONResponse({"responder": "David", "response": "Noted, Dan. I'll route that to the team now."})

    # Parse JSON (LLM sometimes wraps in code fences)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`").split("\n", 1)[-1]
        if txt.endswith("```"):
            txt = txt.rsplit("```", 1)[0]
    try:
        parsed = json.loads(txt)
        responder = parsed.get("responder", "").strip() or "David"
        response = parsed.get("response", "").strip() or "Noted, Dan."
    except Exception:
        responder, response = "David", txt.splitlines()[0][:240]

    # Guardrail: ensure responder is an actual attendee
    if responder not in attendees:
        # Best-effort fuzzy match
        low = responder.lower()
        match = next((a for a in attendees if a.lower() == low), None)
        responder = match or "David"

    return JSONResponse({"responder": responder, "response": response})


@app.post("/api/meeting/replan")
async def api_meeting_replan(request: Request):
    """Given the remaining segments + user's question + agent's answer,
    return a RECALIBRATED list of remaining segments that incorporates the
    new context. Agents may shift their take based on what Dan asked."""
    data = await request.json()
    remaining = data.get("remaining") or []      # [{speaker,text,role,pause_ms_after}, ...]
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    answerer = (data.get("answerer") or "").strip()
    subject = (data.get("subject") or "").strip()
    attendees = data.get("attendees") or []

    if not remaining or not question:
        return JSONResponse({"segments": remaining, "changed": False})

    # Build context for the LLM
    attendees_str = ", ".join(a for a in attendees if a)
    remaining_str = "\n".join(f"{s.get('speaker','?')}: {s.get('text','')}" for s in remaining)
    system = (
        "You are the meeting director. Dan just asked a clarifying question mid-meeting and an "
        "agent answered. Now REPLAN the remaining meeting turns so they incorporate this new "
        "context naturally. Keep the same speakers where possible, but update what they say so "
        "the conversation flows smoothly from the new information. Do NOT add or remove turns; "
        "keep the same count. Return STRICT JSON: "
        '{"segments":[{"speaker":"...","text":"...","role":"agent|orchestrator|dan","pause_ms_after":300}, ...]}. '
        f"Known attendees: {attendees_str}. Keep responses short (≤2 sentences each)."
    )
    user = (
        f"Meeting subject: {subject}\n\n"
        f"Dan interjected: \"{question}\"\n"
        f"{answerer} answered: \"{answer}\"\n\n"
        f"Remaining segments to replan (keep count = {len(remaining)}):\n{remaining_str}\n\n"
        "Return JSON only."
    )
    raw = await _call_openrouter("google/gemini-2.5-flash", system, user)
    if not raw:
        return JSONResponse({"segments": remaining, "changed": False})

    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`").split("\n", 1)[-1]
        if txt.endswith("```"):
            txt = txt.rsplit("```", 1)[0]
    try:
        parsed = json.loads(txt)
        new_segs = parsed.get("segments") or []
    except Exception:
        return JSONResponse({"segments": remaining, "changed": False})

    # Guardrails: same length, valid speakers, sane text lengths
    if len(new_segs) != len(remaining):
        return JSONResponse({"segments": remaining, "changed": False})

    valid = set(attendees) | {"Dan", "David", "Narrator"}
    out = []
    for orig, nxt in zip(remaining, new_segs):
        speaker = nxt.get("speaker") or orig.get("speaker")
        if speaker not in valid:
            speaker = orig.get("speaker")
        text = (nxt.get("text") or "").strip()[:600] or orig.get("text", "")
        out.append({
            "speaker": speaker,
            "text": text,
            "role": nxt.get("role") or orig.get("role") or "agent",
            "pause_ms_after": int(orig.get("pause_ms_after") or 300),
        })
    return JSONResponse({"segments": out, "changed": True})


@app.post("/api/meeting/finalize")
async def api_meeting_finalize(request: Request):
    """Append the full Q&A transcript to the original report .md, then run a
    verification pass and persist an 'Updated Analysis' section. This is what
    ensures the task is correct going forward after Dan's interjections."""
    _bump("meetings_finalized")
    data = await request.json()
    name = Path(data.get("name", "")).name
    transcript = data.get("transcript") or []   # [{speaker,text,type}]
    qa_pairs = data.get("qa_pairs") or []       # [{question, responder, response}]

    path = _find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "report not found"}, status_code=404)

    if not qa_pairs:
        return JSONResponse({"ok": True, "updated": False, "reason": "no interjections"})

    # Build the Q&A block
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    qa_block = [f"\n\n---\n\n## 💬 Meeting Interjections — {ts}\n"]
    for i, qa in enumerate(qa_pairs, 1):
        qa_block.append(f"\n### Q{i}. {qa.get('question','').strip()}")
        qa_block.append(f"\n**{qa.get('responder','David')}:** {qa.get('response','').strip()}\n")

    # Run the re-analysis via LLM
    original = path.read_text(encoding="utf-8")
    task = _extract_task_from_report(original)
    qa_text = "\n".join(f"Q: {qa.get('question','')}\nA ({qa.get('responder','')}): {qa.get('response','')}"
                        for qa in qa_pairs)
    system = (
        "You verify the correctness of a task analysis after new clarifications surfaced in a "
        "live meeting. Produce an 'Updated Analysis' paragraph (≤120 words) that integrates the "
        "Q&A into the original finding. End with a single-line verdict: "
        "'VERDICT: CORRECT — proceed' OR 'VERDICT: NEEDS REVISION — <what to change>'."
    )
    user = (
        f"Original task: {task}\n\n"
        f"Original report:\n{original[:4000]}\n\n"
        f"New Q&A surfaced in meeting:\n{qa_text}\n\n"
        "Write the Updated Analysis paragraph + verdict line."
    )
    updated = await _call_openrouter("google/gemini-2.5-flash", system, user) or "(re-analysis unavailable)"
    qa_block.append(f"\n### 🔎 Updated Analysis\n\n{updated.strip()}\n")

    # Append + save
    try:
        path.write_text(original + "".join(qa_block), encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)

    # Invalidate any cached meeting MP3 so next playback regenerates from updated .md
    for d in (MEETINGS_DIR, MEETINGS_SAVED):
        for f in d.glob("*.mp3"):
            stem = f.stem.split("_", 1)[-1]
            if stem in path.stem:
                try: f.unlink()
                except Exception: pass

    result = {
        "ok": True,
        "updated": True,
        "verdict_line": updated.strip().splitlines()[-1] if updated else "",
        "qa_count": len(qa_pairs),
    }
    await _dispatch_webhook("meeting.finalized", {
        "name": name,
        "verdict_line": result["verdict_line"],
        "qa_count": result["qa_count"],
        "tenant_id": _tenant_id(request),
    })
    return JSONResponse(result)


@app.get("/api/meeting/loading-slides")
async def api_meeting_loading_slides(request: Request):
    """Return randomised loading slides + tier info for the meeting room screen.

    Free tier: 5 random slides + a signed watch token the client echoes back
               to /api/meeting/script to prove it went through the ad flow.
    Pro tier:  empty slides list, zero wait, no token required.

    When SEMECLAW_ADS_URL is configured the request is proxied to the central
    AdClaw server. Impressions are logged there atomically on fetch — no client
    trust required, works even across open-source self-hosted forks.
    """
    tier = _get_tier(request)

    if tier == "pro":
        return JSONResponse({"tier": "pro", "wait_ms": 0, "slides": [], "token": None})

    ip = (
        request.headers.get("x-forwarded-for") or
        (request.client.host if request.client else "unknown")
    ).split(",")[0].strip()

    # --- AdClaw proxy path ---
    if SEMECLAW_ADS_URL:
        try:
            async with httpx.AsyncClient(timeout=4.0) as _c:
                _r = await _c.get(
                    f"{SEMECLAW_ADS_URL}/api/slides/next",
                    params={
                        "instance_id": SEMECLAW_INSTANCE_ID,
                        "ip_hash": _hash_ip(ip),
                        "tenant_id": SEMECLAW_TENANT_ID,
                    },
                )
                if _r.status_code == 200:
                    data = _r.json()
                    data.setdefault("wait_ms", FREE_TIER_WAIT_SECONDS * 1000)
                    data.setdefault("tier", "free")
                    return JSONResponse(data)
        except Exception as _ads_err:
            logger.warning("AdClaw proxy failed, falling back to local slides: %s", _ads_err)

    # --- In-process AdClaw fallback (when no external AdClaw URL is set) ---
    if _adclaw_get_next_slide:
        try:
            return await _adclaw_get_next_slide(
                instance_id=SEMECLAW_INSTANCE_ID,
                ip_hash=_hash_ip(ip),
                tenant_id=SEMECLAW_TENANT_ID,
                count=5,
            )
        except Exception as _adclaw_err:
            logger.warning("In-process AdClaw serving failed, falling back to local slides: %s", _adclaw_err)

    # --- Local fallback (always available, no impression tracking) ---
    all_slides = _load_slides()
    count = min(5, len(all_slides))
    slides = _random.sample(all_slides, count) if len(all_slides) >= count else list(all_slides)
    token = _issue_watch_token_v2(ip)

    return JSONResponse({
        "tier": "free",
        "wait_ms": FREE_TIER_WAIT_SECONDS * 1000,
        "slides": slides,
        "token": token,
    })


@app.post("/api/spotlight/impression")
async def api_spotlight_impression(request: Request):
    """Log a Sponsored Spotlight impression for billing (1 credit = 1 view).
    Proxies to AdClaw if SEMECLAW_ADS_URL configured, else local logging."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    ad_id = body.get("ad_id", "unknown")
    slide_count = body.get("slide_count", 0)
    ip = ((request.headers.get("x-forwarded-for") or "") or
          (request.client.host if request.client else "unknown")).split(",")[0].strip()

    if SEMECLAW_ADS_URL:
        try:
            async with httpx.AsyncClient(timeout=4.0) as _c:
                await _c.post(
                    f"{SEMECLAW_ADS_URL}/api/impressions/log",
                    json={
                        "ad_id": ad_id,
                        "instance_id": SEMECLAW_INSTANCE_ID,
                        "ip_hash": _hash_ip(ip),
                        "slide_count": slide_count,
                    },
                )
        except Exception as _e:
            logger.warning("AdClaw impression proxy failed (non-fatal): %s", _e)
    else:
        logger.info("Spotlight impression: ad=%s slides=%s ip_hash=%s", ad_id, slide_count, _hash_ip(ip))

    return JSONResponse({"ok": True})


@app.get("/api/meeting/script")
async def api_meeting_script(name: str, lang: str = "en", request: Request = None):
    """Convert a report into a playable meeting script. Translates when lang != en.

    Free tier: enforces FREE_TIER_WAIT_SECONDS server-side delay regardless of client.
    Pro tier:  no delay — provide X-SemeClaw-License header with a valid pro key.
    """
    from meeting_skill import build_script

    path = _find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "not found"}, status_code=404)
    content = path.read_text(encoding="utf-8")
    task = _extract_task_from_report(content)
    run_id = _lookup_run_id_for_task(task) or path.stem

    script = build_script(report_content=content, task=task, meeting_id=run_id)
    payload = script.to_dict()

    if lang and lang != "en":
        cache_stem = f"{payload['meeting_id']}_{lang}.json"
        cache_path = SCRIPTS_CACHE_DIR / cache_stem
        if cache_path.exists():
            try:
                return JSONResponse(json.loads(cache_path.read_text(encoding="utf-8")))
            except Exception:
                cache_path.unlink(missing_ok=True)
        payload["segments"] = await _translate_script(payload["segments"], lang)
        payload["lang"] = lang
        try:
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass
    else:
        payload["lang"] = "en"

    # Free-tier gate: server-enforced wait so the loading screen can't be skipped
    # even by calling this endpoint directly (e.g. from a custom client or curl).
    if request and _get_tier(request) == "free" and FREE_TIER_WAIT_SECONDS > 0:
        await asyncio.sleep(FREE_TIER_WAIT_SECONDS)

    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Persistent audio cache for meetings
# ---------------------------------------------------------------------------

AUDIO_DIR        = WAR_ROOM_DIR / "audio"
MEETINGS_DIR     = AUDIO_DIR / "meetings"              # rolling — 48h retention
MEETINGS_SAVED   = AUDIO_DIR / "meetings" / "saved"    # pinned — kept forever
SEGMENTS_DIR     = AUDIO_DIR / "segments"
for d in (AUDIO_DIR, MEETINGS_DIR, MEETINGS_SAVED, SEGMENTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

MEETING_RETENTION_HOURS = 48
REPORT_RETENTION_HOURS = 48

RESEARCH_SAVED = RESEARCH_DIR / "saved"
RESEARCH_SAVED.mkdir(parents=True, exist_ok=True)


def _find_report(name: str) -> Path | None:
    """Find a report by filename, checking saved/ first, then rolling."""
    safe = Path(name).name
    for d in (RESEARCH_SAVED, RESEARCH_DIR):
        p = d / safe
        if p.exists() and p.is_file():
            return p
    return None


def _find_cached_meeting(stem_prefix: str) -> Path | None:
    """Look for a cached meeting file (saved first, then rolling)."""
    for d in (MEETINGS_SAVED, MEETINGS_DIR):
        for f in d.glob(f"{stem_prefix}*.mp3"):
            return f
    return None


def _prune_old() -> dict[str, int]:
    """Delete unpinned meeting MP3s and report MDs older than their retention window."""
    import time
    now = time.time()
    out = {"meetings": 0, "reports": 0}

    # Meetings (48h)
    m_cutoff = now - MEETING_RETENTION_HOURS * 3600
    for f in MEETINGS_DIR.glob("*.mp3"):
        if f.parent == MEETINGS_SAVED:
            continue
        try:
            if f.stat().st_mtime < m_cutoff:
                f.unlink()
                out["meetings"] += 1
        except Exception:
            pass

    # Reports (48h) — rolling only, skip saved/
    r_cutoff = now - REPORT_RETENTION_HOURS * 3600
    for f in RESEARCH_DIR.glob("*.md"):
        if f.parent == RESEARCH_SAVED:
            continue
        try:
            if f.stat().st_mtime < r_cutoff:
                f.unlink()
                out["reports"] += 1
        except Exception:
            pass

    # 100-task cap on completed_tasks in shared_state.json
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            tasks = state.get("completed_tasks", [])
            if len(tasks) > 100:
                tasks_sorted = sorted(
                    tasks,
                    key=lambda t: t.get("completed_at", ""),
                    reverse=True,
                )
                kept = tasks_sorted[:100]
                removed = tasks_sorted[100:]
                # Delete report files for evicted tasks
                for evicted in removed:
                    rname = evicted.get("report_name") or evicted.get("report")
                    if rname:
                        rpath = RESEARCH_DIR / rname
                        try:
                            if rpath.exists() and rpath.parent != RESEARCH_SAVED:
                                rpath.unlink()
                                out["reports"] += 1
                        except Exception:
                            pass
                state["completed_tasks"] = kept
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("_prune_old 100-task cap error: %s", e)

    return out


# Backward-compat alias (old call sites)
def _prune_old_meetings() -> int:
    return _prune_old()["meetings"]


def _meeting_cache_path(meeting_id: str, safe_name: str) -> Path:
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", safe_name.removesuffix(".md"))[:80]
    return MEETINGS_DIR / f"{meeting_id}_{stem}.mp3"


async def _synthesize_segment(client: httpx.AsyncClient, speaker: str, text: str) -> bytes | None:
    """Call our own /api/tts via localhost to leverage the existing ElevenLabs/edge-tts pipeline."""
    try:
        resp = await client.get(
            "http://127.0.0.1:8765/api/tts",
            params={"text": text, "speaker": speaker, "lang": "en"},
            timeout=30.0,
        )
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:
        logger.warning(f"segment synth failed for {speaker}: {e}")
    return None


async def _build_meeting_mp3(name: str) -> Path | None:
    """Generate + cache the concatenated meeting MP3 for a report. Returns cached path."""
    import re as _re, shutil, subprocess, tempfile
    from meeting_skill import build_script

    report_path = _find_report(name)
    if not report_path:
        return None
    safe_name = report_path.name
    content = report_path.read_text(encoding="utf-8")
    task = _extract_task_from_report(content)
    meeting_id = _lookup_run_id_for_task(task) or _re.sub(r"\W", "", safe_name)[:8] or "000"
    script = build_script(report_content=content, task=task, meeting_id=meeting_id)

    cache_path = _meeting_cache_path(script.meeting_id, safe_name)
    if cache_path.exists() and cache_path.stat().st_size > 2048:
        return cache_path

    ffmpeg = shutil.which("ffmpeg") or next(
        (p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg") if Path(p).exists()),
        None,
    )
    if not ffmpeg:
        logger.warning("ffmpeg not found — cannot build meeting MP3 cache")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        list_lines: list[str] = []

        async with httpx.AsyncClient() as client:
            for i, seg in enumerate(script.segments):
                audio = await _synthesize_segment(client, seg.speaker, seg.text)
                if not audio:
                    continue
                seg_path = tmp_dir / f"{i:02d}_{seg.speaker}.mp3"
                seg_path.write_bytes(audio)
                list_lines.append(f"file '{seg_path}'")

                pause_ms = max(0, min(1500, seg.pause_ms_after))
                if pause_ms:
                    sil = tmp_dir / f"sil_{pause_ms}.mp3"
                    if not sil.exists():
                        subprocess.run(
                            [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                             "-t", f"{pause_ms/1000:.2f}", "-q:a", "2", str(sil)],
                            capture_output=True,
                        )
                    list_lines.append(f"file '{sil}'")

        if not list_lines:
            return None

        list_path = tmp_dir / "concat.txt"
        list_path.write_text("\n".join(list_lines))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c:a", "libmp3lame", "-q:a", "2", str(cache_path)],
            capture_output=True,
        )

    if not cache_path.exists() or cache_path.stat().st_size < 2048:
        return None
    return cache_path


@app.get("/api/meeting/audio")
async def api_meeting_audio(name: str, download: bool = False):
    """Return the cached meeting MP3 for a report (generated on first call)."""
    from fastapi.responses import FileResponse

    path = await _build_meeting_mp3(name)
    if not path:
        return JSONResponse({"error": "could not build meeting audio"}, status_code=500)
    headers = {"Cache-Control": "public, max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(path, media_type="audio/mpeg", headers=headers)


@app.get("/api/meeting/list")
async def api_meeting_list():
    """List all cached meeting MP3s (rolling + saved). Prunes unsaved ones >48h first."""
    pruned = _prune_old()
    items = []
    for d, saved in ((MEETINGS_SAVED, True), (MEETINGS_DIR, False)):
        for f in sorted(d.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.parent.name == "saved" and not saved:
                continue
            items.append({
                "file":     f.name,
                "saved":    saved,
                "size_kb":  round(f.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
    return JSONResponse({
        "items": items,
        "pruned_this_call": pruned,
        "retention_hours": {"meetings": MEETING_RETENTION_HOURS, "reports": REPORT_RETENTION_HOURS},
    })


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError:
        dest.write_bytes(src.read_bytes())
        src.unlink(missing_ok=True)


@app.post("/api/meeting/pin")
async def api_meeting_pin(name: str):
    """Pin the REPORT + its cached meeting MP3. Both survive 48h cleanup."""
    # 1. Build (or find) the meeting audio
    audio_path = await _build_meeting_mp3(name)
    if not audio_path:
        return JSONResponse({"error": "could not build meeting audio"}, status_code=500)
    if audio_path.parent != MEETINGS_SAVED:
        _move_file(audio_path, MEETINGS_SAVED / audio_path.name)

    # 2. Pin the underlying report .md too
    report_path = _find_report(name)
    if report_path and report_path.parent != RESEARCH_SAVED:
        _move_file(report_path, RESEARCH_SAVED / report_path.name)

    return JSONResponse({
        "ok": True,
        "audio_file": audio_path.name,
        "report_file": Path(name).name,
        "saved": True,
    })


@app.post("/api/meeting/unpin")
async def api_meeting_unpin(name: str = "", file: str = ""):
    """Unpin a meeting + its report. Accepts either the report name or the audio filename."""
    moved = []
    # Report side
    report_name = Path(name).name if name else ""
    if report_name:
        src = RESEARCH_SAVED / report_name
        if src.exists():
            _move_file(src, RESEARCH_DIR / report_name)
            moved.append(report_name)
    # Audio side
    if file:
        src = MEETINGS_SAVED / Path(file).name
        if src.exists():
            _move_file(src, MEETINGS_DIR / Path(file).name)
            moved.append(Path(file).name)
    elif report_name:
        # Try to locate the audio by filename pattern
        for f in MEETINGS_SAVED.glob("*.mp3"):
            if report_name.removesuffix(".md").lower() in f.name.lower():
                _move_file(f, MEETINGS_DIR / f.name)
                moved.append(f.name)
                break
    if not moved:
        return JSONResponse({"error": "nothing to unpin"}, status_code=404)
    return JSONResponse({"ok": True, "moved": moved, "saved": False})


@app.get("/api/logs")
async def api_logs():
    entries = []
    for log_file in sorted(LOGS_DIR.glob("run-*.jsonl"), reverse=True)[:3]:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    entries.sort(key=lambda x: x.get("completed", ""), reverse=True)
    return JSONResponse(entries[:50])


# ---------------------------------------------------------------------------
# Standalone-agent manifest + embed
# ---------------------------------------------------------------------------

# MOVED to routes/agents.py
# @app.get("/api/agent/manifest")
async def api_agent_manifest():
    """Describe what this SemeClaw agent can do. Used by consumers (NERVIX,
    Paperclip companies, direct integrators) to discover capabilities."""
    auth_required = bool(SEMECLAW_API_KEY)
    return JSONResponse({
        "id":          "semeclaw-war-room",
        "name":        "SemeClaw War Room",
        "version":     APP_VERSION,
        "tenant":      SEMECLAW_TENANT_ID,
        "public_url":  SEMECLAW_PUBLIC_URL,
        "description": ("Cinematic AI agent meeting room. Converts any task report "
                        "into a scripted multi-agent dialogue with voice, user "
                        "interjections (2-question budget), live recalibration, "
                        "and task re-analysis."),
        "capabilities": [
            "meeting.script",      # turn a report into scripted segments
            "meeting.audio",       # generate + cache MP3 of the meeting
            "meeting.redirect",    # pick best agent to answer a question
            "meeting.replan",      # rewrite remaining segments given Q&A
            "meeting.finalize",    # append Q&A + re-analyze source task
            "meeting.pin",         # save meeting+report beyond 48h
            "meeting.share",       # public share links (30d TTL)
            "meeting.events.sse",  # live SSE stream of lifecycle events
            "reports.list",
            "reports.content",
            "reports.create",      # external systems ingest reports
            "reports.upload",      # multipart .md upload
            "reports.delete",
            "tts.synthesize",      # ElevenLabs Flash v2.5 → edge-tts fallback
            "embed.iframe",        # renders inside an iframe
            "embed.widget",        # script-tag SDK
            "webhooks.register",   # HMAC-signed lifecycle webhooks
            "metrics.prometheus",  # /metrics scrape target
            "tenants.isolation",   # X-Tenant-Id header
            "paperclip.trigger",   # one-shot meeting from a Paperclip task
            "paperclip.card",      # first-class agent-card manifest
            "templates.list",      # meeting templates (bug-triage, sprint-planning, etc.)
            "templates.use",       # convene meeting from a template
            "voices.override",     # per-tenant speaker→voice override
            "costs.ledger",        # per-tenant usage + cost snapshot
            "layout.theater",      # V3 fullscreen-speaker UI
            "voices.clone",        # ElevenLabs Instant Voice Clone
            "transcripts.srt",     # SRT subtitle export
            "transcripts.html",    # printable HTML export
            "billing.stripe",      # Stripe scaffold (opt-in via env)
            "integrations.slack",  # /semeclaw slash command bot
            "integrations.github", # GitHub Action for PR meetings
            "skills.registry",     # agent skill cards — who knows what
            "meeting.agents",      # discover agents in a specific meeting
            "meeting.inject",      # human injects requirement/question mid-meeting
            "compound.engineering",# compound-engineering-plugin integration
        ],
        "endpoints": {
            "health":      "/api/agent/health",
            "manifest":    "/api/agent/manifest",
            "reports":     "/api/reports",
            "report":      "/api/reports/content?name={name}",
            "report_create": "POST /api/reports",
            "report_upload": "POST /api/reports/upload",
            "report_delete": "DELETE /api/reports?name={name}",
            "script":      "/api/meeting/script?name={name}&lang=en",
            "audio":       "/api/meeting/audio?name={name}",
            "redirect":    "/api/meeting/redirect",
            "replan":      "/api/meeting/replan",
            "finalize":    "/api/meeting/finalize",
            "pin":         "/api/meeting/pin?name={name}",
            "unpin":       "/api/meeting/unpin?file={file}&name={name}",
            "list":        "/api/meeting/list",
            "share":       "GET /api/meeting/share?name={name}",
            "events_sse":  "/api/events?tenant={id}&events={csv}",
            "tts":         "/api/tts?text={text}&speaker={speaker}&lang=en",
            "embed_html":  "/embed?meeting={name}&v=2",
            "embed_js":    "/embed.js",
            "metrics":     "/metrics",
            "webhooks":    "/api/webhooks",
            "paperclip_card":    "/api/paperclip/agent-card",
            "paperclip_trigger": "POST /api/paperclip/trigger",
            # Skill registry — human interaction protocol
            "skills_list":   "/api/agents/skills",
            "skill_detail":  "/api/agents/skills/{skill_id}",
            "meeting_agents":"/api/meeting/agents?name={name}",
            "inject":        "POST /api/meeting/inject",
        },
        "auth": {
            "required_for_writes": auth_required,
            "scheme":   "bearer" if auth_required else "none",
            "header":   "Authorization: Bearer <SEMECLAW_API_KEY>" if auth_required else None,
            "protected_paths": list(_PROTECTED_WRITE_PATHS) if auth_required else [],
        },
        "tts": {
            "engines":         ["elevenlabs-flash-v2.5", "kokoro-82M"],
            "languages":       ["en"],
            "voice_map_size":  len(_ELEVEN_VOICES),
            "kokoro_voices":   ["af_bella", "af_nicole", "am_adam", "bf_emma", "bm_george"],
        },
        "stt": {
            "engine":          "faster-whisper",
            "model":           "large-v3-turbo",
            "languages":       "99+ (auto-detected)",
            "license":         "MIT",
        },
        "retention": {
            "meetings_hours": MEETING_RETENTION_HOURS,
            "reports_hours":  REPORT_RETENTION_HOURS,
            "pin_to_save":    True,
        },
        "layouts":  ["v1-flat", "v2-orbital"],
        "meeting_budget": {
            "max_user_questions_per_meeting": 2,
            "recalibration": "orchestrator/hermes",
            "finalize_verdict_line": True,
        },
        "integrations": {
            "paperclip": True,
            "nervix":    "planned-phase-3",
            "supabase":  True,
            "telegram":  False,
        },
    })


# ---------------------------------------------------------------------------
# Meeting Execute — synthesize War Room output into production code + assets
# ---------------------------------------------------------------------------

# Builds are stored here: war_room/builds/{meeting_name}/
_BUILDS_DIR = WAR_ROOM_DIR / "builds"
_BUILDS_DIR.mkdir(exist_ok=True)


@app.post("/api/meeting/execute")
async def api_meeting_execute(request: Request):
    """Synthesize a finalized War Room meeting into production code.

    Reads all agent contributions from the meeting report, then calls an LLM
    to generate a complete, deployable single-file HTML+CSS+JS artifact.
    Stores the result in war_room/builds/{meeting_name}/ and returns a
    preview URL.

    Request body:
    {
      "name":    "report filename",
      "type":    "webpage" | "dashboard" | "landing" | "doc" (default: "webpage"),
      "deploy":  false  // set true to also push to Vercel
    }
    """
    data = await request.json()
    name   = Path(data.get("name", "")).name
    kind   = (data.get("type") or "webpage").strip().lower()

    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    report_path = _find_report(name)
    if report_path is None:
        return JSONResponse({"error": "report not found"}, status_code=404)

    content = report_path.read_text(encoding="utf-8")

    # ── Build the synthesis prompt ────────────────────────────────────────
    system = """You are a senior full-stack engineer and UI designer.
You have just attended a War Room meeting where multiple specialist agents
produced research, strategy, copy, and architecture guidance.

Your job: synthesize ALL of their output into a single, production-ready
HTML file (inline CSS + inline JS, no external dependencies except Google Fonts).

Design system — "DansLab Claude Edition":
- Palette: background #080808, surface #111111, border #1e1e1e
  accent-gold #D4A853, accent-glow rgba(212,168,83,0.15)
  text-primary #F0EDE8, text-muted #888880
- Typography: 'Inter' (Google Fonts) — hero 72-96px, section 48px, body 16-18px
- Motion: subtle fade-in on scroll (IntersectionObserver), hover lifts
- Style: dark luxury — think Anthropic.com meets a Y Combinator portfolio site.
  Strong hierarchy, generous whitespace, no gradients, precise grid.
  Cards have 1px #1e1e1e border + soft box-shadow: 0 0 40px rgba(0,0,0,0.8)
- Must be self-contained: single HTML file, no external JS, minimal CDN (fonts only)
- Must be mobile-responsive with a hamburger nav
- No placeholder text — use ONLY real content from the meeting

Extract from each agent section and use verbatim where appropriate:
- Research Agent → facts, competitive context, market position
- Strategist/GSD → structure, priorities, messaging hierarchy
- Writer/Hermes → all copy (headlines, body, CTAs) — use their exact words
- Architect/David → any technical decisions to feature
- Coder/Dexter → any implementation notes to honour

Return ONLY the complete HTML document, starting with <!DOCTYPE html>.
No markdown, no explanation, no code fences. Just raw HTML."""

    user = f"""Meeting report content:

{content}

Generate the complete production HTML file for a {kind}.
Start immediately with <!DOCTYPE html>."""

    raw = await _call_openrouter_large("google/gemini-2.5-pro", system, user)

    if not raw:
        # Fallback 2: Claude Sonnet via OpenRouter
        raw = await _call_openrouter_large("anthropic/claude-sonnet-4-6", system, user)

    if not raw:
        # Fallback 3: local Gemma 4 (9.6GB, free, no rate limits)
        logger.info("OpenRouter unavailable — falling back to local Gemma 4")
        raw = await _call_ollama_large("gemma4:latest", system, user)

    if not raw:
        # Fallback 4: local Qwen3 8B (lightweight but can generate HTML)
        raw = await _call_ollama_large("qwen3:8b", system, user)

    if not raw:
        return JSONResponse({"error": "LLM synthesis failed — all models unavailable"}, status_code=502)

    # Strip any accidental markdown fences
    html = raw.strip()
    if html.startswith("```"):
        html = html.split("\n", 1)[-1]
        if html.endswith("```"):
            html = html.rsplit("```", 1)[0]
    html = html.strip()

    # Ensure it starts with a valid doctype
    if not html.lower().startswith("<!doctype"):
        idx = html.lower().find("<!doctype")
        if idx >= 0:
            html = html[idx:]

    # ── Persist ───────────────────────────────────────────────────────────
    build_slug = name.replace(".md", "").rstrip(".")
    build_dir  = _BUILDS_DIR / build_slug
    build_dir.mkdir(parents=True, exist_ok=True)
    index_path = build_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")

    preview_url = f"/build/{build_slug}/index.html"
    return JSONResponse({
        "ok":           True,
        "build_slug":   build_slug,
        "preview_url":  preview_url,
        "public_url":   f"{SEMECLAW_PUBLIC_URL}{preview_url}",
        "file_size_kb": round(len(html.encode()) / 1024, 1),
        "kind":         kind,
        "message":      "War Room synthesis complete. Open preview_url to view the result.",
    })


@app.get("/build/{build_slug}/{filename}")
async def api_build_file(build_slug: str, filename: str):
    """Serve files from a completed War Room build."""
    safe_slug = Path(build_slug).name
    safe_file = Path(filename).name
    file_path  = _BUILDS_DIR / safe_slug / safe_file
    if not file_path.exists():
        return JSONResponse({"error": "build file not found"}, status_code=404)
    media = "text/html" if safe_file.endswith(".html") else "text/plain"
    return Response(
        content=file_path.read_bytes(),
        media_type=media,
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/meeting/builds")
async def api_meeting_builds():
    """List all completed War Room builds."""
    builds = []
    if _BUILDS_DIR.exists():
        for d in sorted(_BUILDS_DIR.iterdir()):
            if d.is_dir():
                index = d / "index.html"
                builds.append({
                    "slug":        d.name,
                    "preview_url": f"/build/{d.name}/index.html",
                    "size_kb":     round(index.stat().st_size / 1024, 1) if index.exists() else 0,
                    "modified":    datetime.fromtimestamp(
                        index.stat().st_mtime, tz=timezone.utc
                    ).isoformat() if index.exists() else None,
                })
    return JSONResponse({"count": len(builds), "builds": builds})


# ---------------------------------------------------------------------------
# Skill Registry — agent skill discovery and human interaction protocol
# ---------------------------------------------------------------------------

# Skills directory lives at SemeClaw/skills/ (project root)
_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Speaker-to-skill-id mapping (mirrors _SECTION_ROUTES in meeting_skill.py)
_SPEAKER_TO_SKILL: dict[str, str] = {
    "Autoresearch": "autoresearch",
    "Discovery":    "autoresearch",  # alias
    "GSD":          "gsd",
    "Hermes":       "hermes",
    "David":        "david",
    "Dexter":       "dexter",
    "Narrator":     "narrator",
    # Additional speakers that may appear in meetings but have no skill file
    "Dan":          "",
    "Doctor":       "",
    "Monitor":      "",
    "Finance":      "",
    "Growth":       "",
    "Learning":     "",
    "N8N":          "",
    "Obsidian":     "",
    "Codex":        "",
    "Claude Code":  "",
}


def _load_skill(skill_id: str) -> dict | None:
    """Load and parse a SKILL.md file from the skills directory."""
    path = _SKILLS_DIR / f"{skill_id}.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")

    # Split YAML frontmatter from body
    parts = raw.split("---", 2)
    meta: dict = {}
    body = raw
    if len(parts) >= 3:
        import yaml as _yaml  # optional — fall back to manual parse
        try:
            meta = _yaml.safe_load(parts[1]) or {}
        except Exception:
            # Manual parse for key: value lines
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"')
        body = parts[2].strip()

    return {
        "id":           meta.get("id", skill_id),
        "name":         meta.get("name", skill_id),
        "speaker":      meta.get("speaker", ""),
        "paperclip_agent": meta.get("paperclip_agent"),
        "role":         meta.get("role", ""),
        "version":      meta.get("version", "1.0.0"),
        "triggers":     meta.get("triggers") or [],
        "interacts_with": meta.get("interacts_with") or [],
        "shared_files": meta.get("shared_files") or [],
        "how_to_invoke": meta.get("how_to_invoke", ""),
        "human_interaction": (meta.get("human_interaction") or "").strip(),
        "body":         body,
        "source":       f"skills/{skill_id}.md",
    }


def _all_skills() -> list[dict]:
    """Return all available skill cards (summary only)."""
    skills = []
    if not _SKILLS_DIR.exists():
        return skills
    for p in sorted(_SKILLS_DIR.glob("*.md")):
        skill = _load_skill(p.stem)
        if skill:
            skills.append(skill)
    return skills


def _skill_for_speaker(speaker: str) -> dict | None:
    """Look up a skill card by speaker name."""
    skill_id = _SPEAKER_TO_SKILL.get(speaker, "")
    if not skill_id:
        return None
    return _load_skill(skill_id)


@app.get("/api/agents/skills")
async def api_agents_skills():
    """List all War Room agent skill cards.

    Returns each agent's id, name, role, triggers, interaction graph, and
    human interaction protocol — so humans and external systems can discover
    who knows what before or during a meeting.
    """
    skills = _all_skills()
    return JSONResponse({
        "count": len(skills),
        "skills_dir": str(_SKILLS_DIR),
        "agents": [
            {
                "id":             s["id"],
                "name":           s["name"],
                "speaker":        s["speaker"],
                "role":           s["role"],
                "version":        s["version"],
                "triggers":       s["triggers"],
                "how_to_invoke":  s["how_to_invoke"],
                "interacts_with": s["interacts_with"],
                "human_interaction": s["human_interaction"],
                "paperclip_agent":   s["paperclip_agent"],
                "source":         s["source"],
            }
            for s in skills
        ],
    })


@app.get("/api/agents/skills/{skill_id}")
async def api_agents_skill_detail(skill_id: str):
    """Full skill card for a specific agent (includes the full markdown body).

    Use this to understand exactly what an agent knows, how to talk to it,
    and how it hands off to other agents.
    """
    skill = _load_skill(skill_id)
    if skill is None:
        return JSONResponse({"error": f"skill '{skill_id}' not found"}, status_code=404)
    return JSONResponse(skill)


@app.get("/api/meeting/agents")
async def api_meeting_agents(name: str = ""):
    """Return skill cards for every agent present in a specific meeting.

    Given a meeting report name, parses the script to find which speakers
    appear, then returns their full skill cards. Use this to know who is in
    the room and how to interact with each of them.
    """
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    # Load the report to extract speakers
    report_path = _find_report(name)
    if report_path is None:
        return JSONResponse({"error": "report not found"}, status_code=404)

    content = report_path.read_text(encoding="utf-8")

    # Extract ## headings to find which agents are in this meeting
    heading_re = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    from meeting_skill import _route_section
    speakers_seen: dict[str, bool] = {}
    for m in heading_re.finditer(content):
        speaker = _route_section(m.group(1))
        speakers_seen[speaker] = True

    # Always include David and Dan (orchestrator + boss)
    speakers_seen["David"] = True
    speakers_seen["Dan"] = True
    speakers_seen["Narrator"] = True

    agent_cards = []
    for speaker in speakers_seen:
        skill = _skill_for_speaker(speaker)
        if skill:
            agent_cards.append({
                "id":               skill["id"],
                "name":             skill["name"],
                "speaker":          speaker,
                "role":             skill["role"],
                "how_to_invoke":    skill["how_to_invoke"],
                "human_interaction": skill["human_interaction"],
                "triggers":         skill["triggers"],
                "interacts_with":   skill["interacts_with"],
            })
        else:
            # Speaker present but no skill card — return minimal entry
            agent_cards.append({
                "id":     speaker.lower().replace(" ", "_"),
                "name":   speaker,
                "speaker": speaker,
                "role":   "Specialist",
                "how_to_invoke": f"Ask {speaker} directly about their domain.",
                "human_interaction": "",
                "triggers": [],
                "interacts_with": [],
            })

    return JSONResponse({
        "meeting": name,
        "agent_count": len(agent_cards),
        "agents": agent_cards,
        "human_guide": (
            "To interact with any agent mid-meeting: POST /api/meeting/inject with "
            "your message and the agent's id (or leave agent_id blank for auto-routing). "
            "The meeting will recalibrate remaining segments based on your input."
        ),
    })


@app.post("/api/meeting/inject")
async def api_meeting_inject(request: Request):
    """Human injects new requirements or a question into a live meeting.

    This is the primary human-in-the-loop endpoint. Unlike /redirect (pick
    one agent to answer), inject handles three scenarios:

    1. Question → auto-routes to best agent, returns answer
    2. New requirement → recalibrates all remaining segments
    3. Both → routes + answers + recalibrates in one call

    Request body:
    {
      "message":    "string — the human's message",
      "intent":     "question" | "requirement" | "both" (default: "both"),
      "agent_id":   "string — optional, force route to this agent speaker name",
      "meeting":    {
        "name":      "report name",
        "subject":   "meeting subject",
        "attendees": ["David", "GSD", "Autoresearch", ...],
        "history":   [{speaker, text}, ...],   // last N transcript turns
        "remaining": [{speaker, text, role, pause_ms_after}, ...]  // segments not yet played
      }
    }

    Response:
    {
      "responder":   "AgentName",
      "response":    "The agent's answer",
      "responder_skill": { ...skill card summary... },
      "recalibrated_segments": [...],  // updated remaining segments (may be empty if intent=question)
      "recalibrated": bool,
      "inject_id":  "uuid"
    }
    """
    _bump("questions_asked")
    import uuid as _uuid_inj

    data = await request.json()
    message   = (data.get("message") or "").strip()
    intent    = (data.get("intent") or "both").strip().lower()
    forced_agent = (data.get("agent_id") or "").strip()

    meeting   = data.get("meeting") or {}
    name      = (meeting.get("name") or "").strip()
    subject   = (meeting.get("subject") or "").strip()
    attendees = meeting.get("attendees") or []
    history   = meeting.get("history") or []
    remaining = meeting.get("remaining") or []

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    inject_id = str(_uuid_inj.uuid4())[:8]

    # ---------------------------------------------------------------
    # Step 1: Route to best agent (or use forced agent)
    # ---------------------------------------------------------------
    responder  = forced_agent if forced_agent else ""
    response   = ""

    if intent in ("question", "both") or not remaining:
        # Build skill context for routing — tell LLM what each agent knows
        skill_summaries = []
        skills = _all_skills()
        for s in skills:
            if s["speaker"] in attendees:
                skill_summaries.append(
                    f"- {s['speaker']} ({s['role']}): {s['how_to_invoke']} "
                    f"Triggers: {', '.join(s['triggers'][:5])}"
                )
        skill_context = "\n".join(skill_summaries) if skill_summaries else "No skill cards available."

        attendees_str = ", ".join(a for a in attendees if a not in ("Narrator", "Dan"))
        history_str   = "\n".join(
            f"{h.get('speaker','?')}: {h.get('text','')[:300]}" for h in history[-8:]
        )

        if forced_agent and forced_agent in attendees:
            responder = forced_agent
            # Still generate a response from the forced agent
            system = (
                f"You are {forced_agent} in a live war-room meeting. "
                f"Dan or a human attendee has sent you a direct message. "
                f"Respond in character, concisely (≤3 sentences). "
                f"Return STRICT JSON: {{\"response\":\"<your answer>\"}}"
            )
            user = (
                f"Meeting subject: {subject}\n"
                f"Recent transcript:\n{history_str}\n\n"
                f"Human message directed to you: {message}\n\n"
                "Return only JSON."
            )
        else:
            system = (
                "You are the orchestrator of a live war-room meeting. A human has sent a message. "
                "Pick the single best agent to respond based on their skill domain. "
                "Return STRICT JSON: {\"responder\":\"<AgentName>\",\"response\":\"<answer ≤3 sentences>\"}. "
                "Stay in character for the chosen agent."
            )
            user = (
                f"Meeting subject: {subject}\n"
                f"Attendees (pick one): {attendees_str}\n\n"
                f"Agent skill cards:\n{skill_context}\n\n"
                f"Recent transcript:\n{history_str}\n\n"
                f"Human message: {message}\n\n"
                "Return only JSON."
            )

        raw = await _call_openrouter("google/gemini-2.5-flash", system, user)
        if raw:
            txt = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            try:
                parsed = json.loads(txt)
                if not forced_agent:
                    responder = (parsed.get("responder") or "David").strip()
                response  = (parsed.get("response") or parsed.get("answer") or "Noted.").strip()
            except Exception:
                if not forced_agent:
                    responder = "David"
                response = txt.splitlines()[0][:300]
        else:
            if not responder:
                responder = "David"
            response = "Noted. I'll factor that into the remaining discussion."

        # Guardrail: ensure responder is an actual attendee
        if responder not in attendees:
            low = responder.lower()
            match = next((a for a in attendees if a.lower() == low), None)
            responder = match or "David"

    # ---------------------------------------------------------------
    # Step 2: Recalibrate remaining segments (if requirement/both)
    # ---------------------------------------------------------------
    recalibrated_segments = remaining
    recalibrated = False

    if intent in ("requirement", "both") and remaining and response:
        attendees_str = ", ".join(a for a in attendees if a)
        remaining_str = "\n".join(
            f"{s.get('speaker','?')}: {s.get('text','')}" for s in remaining
        )
        system_r = (
            "You are the meeting director for a live war-room. A human just injected "
            "a new requirement or piece of context into the meeting. REPLAN the remaining "
            "meeting turns so they naturally incorporate this new information. "
            "Keep the same speakers and segment count. Update what they say so the "
            "conversation reflects the new reality. "
            "Return STRICT JSON: "
            "{\"segments\":[{\"speaker\":\"...\",\"text\":\"...\",\"role\":\"agent|orchestrator|dan\","
            "\"pause_ms_after\":300}, ...]}. "
            f"Known attendees: {attendees_str}. Keep responses ≤2 sentences each."
        )
        user_r = (
            f"Meeting subject: {subject}\n\n"
            f"Human injected: \"{message}\"\n"
            f"{responder} responded: \"{response}\"\n\n"
            f"Remaining segments to recalibrate (keep count = {len(remaining)}):\n{remaining_str}\n\n"
            "Return JSON only."
        )
        raw_r = await _call_openrouter("google/gemini-2.5-flash", system_r, user_r)
        if raw_r:
            txt_r = raw_r.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            try:
                parsed_r = json.loads(txt_r)
                segs = parsed_r.get("segments", [])
                if segs and len(segs) == len(remaining):
                    recalibrated_segments = segs
                    recalibrated = True
            except Exception:
                pass  # fall back to original remaining

    # ---------------------------------------------------------------
    # Step 3: Look up the responder's skill card for the client
    # ---------------------------------------------------------------
    responder_skill = _skill_for_speaker(responder)
    responder_skill_summary = None
    if responder_skill:
        responder_skill_summary = {
            "id":            responder_skill["id"],
            "name":          responder_skill["name"],
            "role":          responder_skill["role"],
            "how_to_invoke": responder_skill["how_to_invoke"],
            "interacts_with": responder_skill["interacts_with"],
        }

    # ---------------------------------------------------------------
    # Step 4: Push to Redis dls.interrupts.{agent} for live agent pickup
    # ---------------------------------------------------------------
    if responder and responder.lower() not in ("narrator", "dan"):
        try:
            import redis as _redis_lib
            _r = _redis_lib.Redis(host="localhost", port=6379, decode_responses=True)
            import uuid as _uuid2, json as _json2
            interrupt_record = {
                "id":       f"inj_{inject_id}",
                "agent":    responder.lower(),
                "message":  message,
                "from":     "dan",
                "ts":       __import__("time").time(),
                "task_ref": meeting.get("name"),
                "action":   None,
                "ack":      False,
            }
            _r.xadd(
                f"dls.interrupts.{responder.lower()}",
                {"data": _json2.dumps(interrupt_record)},
                maxlen=100,
            )
            # Also push to generic war room stream
            _r.xadd(
                "dls.warroom",
                {"type": "inject", "data": _json2.dumps({
                    "inject_id": inject_id,
                    "responder": responder,
                    "message":   message[:200],
                })},
                maxlen=500,
            )
        except Exception as _e:
            pass  # Redis unavailable — inject still works, just no Redis push

    return JSONResponse({
        "inject_id":             inject_id,
        "responder":             responder,
        "response":              response,
        "responder_skill":       responder_skill_summary,
        "recalibrated":          recalibrated,
        "recalibrated_segments": recalibrated_segments,
        "intent":                intent,
    })


# MOVED TO routes/embed.py
# @app.get("/embed.js")
# async def api_embed_js():
#     """Tiny JS SDK — drop-in <script> that mounts the War Room in any page.
#
#     Usage:
#         <script src="https://semeclaw.example.com/embed.js"></script>
#         <div data-semeclaw-meeting="ops-review.md"
#              data-semeclaw-v="2"
#              style="width:100%;height:640px"></div>
#     """
#     from fastapi.responses import Response as FResponse
#     base = SEMECLAW_PUBLIC_URL
#     js = f"""(function() {{
#   var BASE = {json.dumps(base)};
#   function mount(el) {{
#     if (el.getAttribute("data-semeclaw-mounted") === "1") return;
#     el.setAttribute("data-semeclaw-mounted", "1");
#     var meeting = el.getAttribute("data-semeclaw-meeting") || "";
#     var layout  = el.getAttribute("data-semeclaw-v") || "1";
#     var theme   = el.getAttribute("data-semeclaw-theme") || "dark";
#     var url = BASE + "/embed?v=" + encodeURIComponent(layout) +
#               "&theme=" + encodeURIComponent(theme) +
#               (meeting ? "&meeting=" + encodeURIComponent(meeting) : "");
#     var iframe = document.createElement("iframe");
#     iframe.src = url;
#     iframe.style.width = el.style.width || "100%";
#     iframe.style.height = el.style.height || "640px";
#     iframe.style.border = "0";
#     iframe.style.borderRadius = el.style.borderRadius || "12px";
#     iframe.setAttribute("allow", "autoplay; clipboard-write");
#     iframe.setAttribute("loading", "lazy");
#     iframe.title = "SemeClaw War Room";
#     el.innerHTML = "";
#     el.appendChild(iframe);
#   }}
#   function scan() {{
#     var nodes = document.querySelectorAll("[data-semeclaw-meeting], [data-semeclaw-embed]");
#     for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
#   }}
#   if (document.readyState === "loading") {{
#     document.addEventListener("DOMContentLoaded", scan);
#   }} else {{
#     scan();
#   }}
#   window.SemeClaw = {{ mount: mount, scan: scan, base: BASE }};
# }})();
# """
#     return FResponse(
#         content=js,
#         media_type="application/javascript; charset=utf-8",
#         headers={"Cache-Control": "public, max-age=300"},
#     )
#
#
# @app.get("/embed/manifest.json")
# async def api_embed_manifest():
#     return JSONResponse({
#         "widget": "semeclaw-war-room",
#         "script_url": f"{SEMECLAW_PUBLIC_URL}/embed.js",
#         "iframe_url": f"{SEMECLAW_PUBLIC_URL}/embed",
#         "min_width":  320,
#         "min_height": 420,
#         "attributes": [
#             {"name": "data-semeclaw-meeting", "required": False, "desc": "Report filename to play"},
#             {"name": "data-semeclaw-v",       "required": False, "desc": "Layout version: 1 | 2 (orbital)"},
#             {"name": "data-semeclaw-theme",   "required": False, "desc": "dark | light (dark only for now)"},
#         ],
#     })
#
#
# @app.get("/embed")
# async def embed_page(meeting: str = "", v: str = "1", theme: str = "dark"):
#     """Serve the dashboard HTML with query-param hints for embed consumers.
#     The main index.html reads window.location.search to auto-open a meeting."""
#     from fastapi.responses import FileResponse
#     from pathlib import Path as _P
#     index = _P(__file__).parent / "index.html"
#     if not index.exists():
#         return JSONResponse({"error": "index not found"}, status_code=500)
#     return FileResponse(index, media_type="text/html",
#                         headers={"X-SemeClaw-Embed": "1",
#                                  "X-SemeClaw-Meeting": meeting or "",
#                                  "X-SemeClaw-Layout": v})
#
#
@app.get("/api/demo/tasks")
async def api_demo_tasks():
    """Return the pre-built demo tasks (only populated in DEMO_MODE)."""
    if not _DEMO_AGENTS:
        return JSONResponse({"tasks": [], "demo": False})
    from demo.loader import DEMO_TASKS
    return JSONResponse({"tasks": DEMO_TASKS, "demo": True})


@app.post("/api/run")
async def api_run(request: Request):
    data = await request.json()
    task = data.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)

    VALID_AGENTS = {"research", "strategist", "architect", "coder", "writer", "narrator", "david"}
    agents = [a for a in (data.get("agents") or []) if a in VALID_AGENTS]
    if not agents:
        agents = list(VALID_AGENTS - {"narrator", "david"})  # sensible default
    project = re.sub(r"[^\w\-]", "", (data.get("project") or "default"))[:80]

    # Broadcast that a task is starting
    await manager.broadcast({
        "type": "task_started",
        "task": task,
        "agents": agents,
        "project": project,
    })

    # Run war_room.py as a subprocess so it doesn't block
    war_room_script = WAR_ROOM_DIR / "war_room.py"
    cmd = [
        sys.executable,
        str(war_room_script),
        "run",
        task,
        f"--agents={','.join(agents)}",
        f"--project={project}",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Monitor the subprocess and broadcast completion
        asyncio.create_task(_monitor_subprocess(proc, task))
        return JSONResponse({"status": "started", "pid": proc.pid, "task": task})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _monitor_subprocess(proc: subprocess.Popen, task: str):
    """Monitor a background war_room process and broadcast when it completes."""
    stdout, _ = proc.communicate()
    await manager.broadcast({
        "type": "task_completed",
        "task": task,
        "exit_code": proc.returncode,
        "output": stdout[-1000:] if stdout else "",
    })
    # Trigger a state refresh broadcast
    if STATE_FILE.exists():
        await manager.broadcast({
            "type": "state_update",
            "state": json.loads(STATE_FILE.read_text()),
        })
    # Auto-generate meeting script in the background so it is ready immediately
    asyncio.create_task(_auto_generate_meeting_for_task(task))


async def _auto_generate_meeting_for_task(task: str):
    """Auto-generate meeting script after pipeline completes. Stored in task_meetings index."""
    await asyncio.sleep(2)  # let report file settle
    try:
        from meeting_skill import build_script

        # Find the newest report matching this task
        reports = sorted(RESEARCH_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        report = next(
            (r for r in reports if task[:30].lower().replace(" ", "-") in r.name.lower()),
            reports[0] if reports else None,
        )
        if not report:
            return

        content = report.read_text(encoding="utf-8")
        run_id = _lookup_run_id_for_task(task) or report.stem[:8]

        # Build meeting script
        script_obj = build_script(report_content=content, task=task, meeting_id=run_id)
        script = script_obj.to_dict()

        # Store in task_meetings index
        _store_task_meeting(task, run_id, report.name, script)

        # Broadcast that meeting is ready for this task
        await manager.broadcast({
            "type": "meeting_ready",
            "task": task,
            "run_id": run_id,
            "report_name": report.name,
            "segment_count": len(script.get("segments", [])),
        })
    except Exception as e:
        logger.error("_auto_generate_meeting_for_task error: %s", e)


# ---------------------------------------------------------------------------
# Paperclip first-class agent adapter — Phase 4 endpoints
# ---------------------------------------------------------------------------

@app.get("/api/paperclip/agent-card")
async def paperclip_agent_card():
    """Paperclip agent-card manifest. A Paperclip control plane can fetch this
    to register SemeClaw War Room as a native agent type on its marketplace."""
    return JSONResponse({
        "agent_type":   "semeclaw.war-room",
        "version":      "0.7.0",
        "name":         "War Room by SemeClaw",
        "icon":         "🎭",
        "description":  "Convene a cinematic multi-agent meeting on any task. "
                        "Host announcer → orchestrator → 5 specialist agents → Dan closes. "
                        "User can interject up to 2 questions; meeting recalibrates live. "
                        "On close, task is re-analyzed with a VERDICT line.",
        "endpoint":     SEMECLAW_PUBLIC_URL,
        "triggers":     ["on_task_comment", "on_task_status_change:review",
                         "manual_convene_meeting"],
        "input_schema": {
            "task_id":        {"type": "string", "required": True},
            "task_markdown":  {"type": "string", "required": True,
                               "desc": "The task content, ideally with ## Agent sections"},
            "task_title":     {"type": "string", "required": False},
            "tenant_id":      {"type": "string", "required": False},
            "auto_audio":     {"type": "boolean", "required": False, "default": True},
            "webhook_url":    {"type": "string", "required": False,
                               "desc": "Posts back meeting.finalized payload"},
        },
        "output_schema": {
            "meeting_id":           "string",
            "report_name":          "string",
            "audio_url":            "string",
            "embed_url":            "string",
            "share_url":            "string (30d TTL)",
            "verdict_line":         "string (populated after finalize)",
            "updated_markdown_url": "string (populated after finalize)",
        },
        "pricing_hint": {"model": "per_meeting", "est_cents": 25},
        "docs_url":     f"{SEMECLAW_PUBLIC_URL}/docs/INTEGRATION.md",
    })


@app.post("/api/paperclip/trigger")
async def paperclip_trigger(request: Request):
    """Convenience endpoint for Paperclip: submit a task, get everything in one shot.

    Body:
        {task_id, task_title, task_markdown, tenant_id?, auto_audio?, webhook_url?}

    Creates the report, optionally builds audio, optionally registers a one-off
    webhook for this specific meeting's finalize event, and returns URLs.
    """
    data = await request.json()
    task_id   = (data.get("task_id") or "").strip() or _uuid_ing.uuid4().hex[:8]
    title     = (data.get("task_title") or task_id).strip()
    markdown  = (data.get("task_markdown") or "").strip()
    tenant    = (data.get("tenant_id") or _tenant_id(request) or "default").strip()
    auto_audio = bool(data.get("auto_audio", True))
    webhook_url = (data.get("webhook_url") or "").strip()

    if not markdown:
        return JSONResponse({"error": "task_markdown required"}, status_code=400)

    # Build a stable report filename from task_id
    stem = _re_ing.sub(r"[^a-zA-Z0-9_-]+", "-", task_id).strip("-").lower()[:60] or "task"
    name = f"pc-{stem}.md"

    # Ensure well-formed header
    if not markdown.lstrip().startswith("#"):
        header = (f"# Paperclip Task · {title}\n\n"
                  f"**Task:** {title}\n"
                  f"**Paperclip ID:** {task_id}\n"
                  f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                  f"**Via:** Paperclip adapter\n\n---\n\n")
        markdown = header + markdown

    # Pick storage dir based on tenant
    if tenant and tenant != "default":
        base = WAR_ROOM_DIR / "tenants" / _re_ing.sub(r"[^a-zA-Z0-9_-]", "-", tenant) / "research"
    else:
        base = RESEARCH_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / name
    path.write_text(markdown, encoding="utf-8")

    # Optional: build audio now
    audio_url = None
    if auto_audio:
        mp3 = await _build_meeting_mp3(name)
        if mp3:
            audio_url = f"{SEMECLAW_PUBLIC_URL}/api/meeting/audio?name={name}"

    # Optional: register one-off webhook for this finalize event
    hook_id = None
    if webhook_url and webhook_url.startswith("http"):
        hooks = _load_webhooks()
        hook_id = _uuid_ing.uuid4().hex[:8]
        hooks.append({
            "id": hook_id, "url": webhook_url,
            "events": ["meeting.finalized"],
            "secret": "", "one_off_meeting": name,
            "source": "paperclip", "paperclip_task_id": task_id,
            "created": datetime.now(timezone.utc).isoformat(),
        })
        _save_webhooks(hooks)

    # Create a share token
    token = _uuid_ing.uuid4().hex[:16]
    expires = int(_time_ing.time()) + SHARE_TTL_DAYS * 86400
    shares = _load_shares()
    shares[token] = {"name": name, "expires": expires,
                     "created": datetime.now(timezone.utc).isoformat(),
                     "source": "paperclip", "paperclip_task_id": task_id}
    _save_shares(shares)

    # Emit a lifecycle event so SSE subscribers get notified immediately
    await _dispatch_webhook("paperclip.triggered", {
        "paperclip_task_id": task_id,
        "report_name": name,
        "tenant_id": tenant,
    })

    return JSONResponse({
        "ok":            True,
        "paperclip_task_id": task_id,
        "report_name":   name,
        "meeting_id":    path.stem,
        "audio_url":     audio_url,
        "embed_url":     f"{SEMECLAW_PUBLIC_URL}/embed?meeting={name}&v=2",
        "share_url":     f"{SEMECLAW_PUBLIC_URL}/share/{token}",
        "script_url":    f"{SEMECLAW_PUBLIC_URL}/api/meeting/script?name={name}",
        "manifest_url":  f"{SEMECLAW_PUBLIC_URL}/api/paperclip/agent-card",
        "webhook_registered": bool(hook_id),
    })


@app.get("/api/paperclip/agents")
async def api_paperclip_agents():
    """Live agent list from Paperclip, sorted by priority."""
    company_id = await _get_company_id()
    if not company_id:
        return JSONResponse({"error": "Paperclip unreachable"}, status_code=503)
    try:
        async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=8.0) as c:
            r = await c.get(f"/api/companies/{company_id}/agents")
            r.raise_for_status()
            agents = r.json() if isinstance(r.json(), list) else []
        # Tier: droplet fleet first, then core Mac Studio AI, then services
        TIER = {"openclaw_gateway": 0, "claude_local": 1, "pi_local": 2,
                "process": 3, "codex_local": 4, "opencode_local": 5, "http": 6}
        STATUS_PRI = {"running": 0, "error": 1, "paused": 2, "idle": 3}
        agents.sort(key=lambda a: (
            TIER.get(a.get("adapter", ""), 9),
            STATUS_PRI.get(a.get("status", "idle"), 9),
            -(a.get("spentCents", 0)),
        ))
        return JSONResponse(agents)
    except Exception as e:
        logger.error("api_paperclip_agents: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/paperclip/agent/{agent_id}")
async def api_paperclip_agent(agent_id: str):
    """Single agent profile + open issues."""
    company_id = await _get_company_id()
    if not company_id:
        return JSONResponse({"error": "Paperclip unreachable"}, status_code=503)
    try:
        async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=8.0) as c:
            r = await c.get(f"/api/companies/{company_id}/agents/{agent_id}")
            r.raise_for_status()
            agent = r.json()
            # Fetch open issues for this agent by name
            try:
                ir = await c.get(f"/api/companies/{company_id}/issues",
                                 params={"assignee": agent.get("name", ""), "status": "todo", "limit": 15})
                ir.raise_for_status()
                data = ir.json()
                agent["open_issues"] = data if isinstance(data, list) else []
            except Exception:
                agent["open_issues"] = []
        return JSONResponse(agent)
    except Exception as e:
        logger.error("api_paperclip_agent: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/meeting")
async def api_meeting():
    """Return shared meeting context history."""
    return JSONResponse(_meeting)


def _get_tg_creds() -> tuple[str, str]:
    """Return (bot_token, chat_id) for Dan alerts.
    Resolution order:
      1. Environment variables (DLS_DAVID_BOT_TOKEN, DLS_DAN_CHAT_ID)
      2. ~/.openclaw/fleet.env
      3. ~/.openclaw/openclaw.json  (danslabmodel account botToken + hardcoded chat_id)
    """
    import os, json as _json
    token = os.environ.get("DLS_DAVID_BOT_TOKEN", "").strip()
    chat  = os.environ.get("DLS_DAN_CHAT_ID", "").strip()

    # 2. fleet.env
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

    # 3. openclaw.json — use danslabmodel account (primary orchestrator bot)
    if not token:
        oc = Path.home() / ".openclaw" / "openclaw.json"
        if oc.exists():
            try:
                cfg = _json.loads(oc.read_text())
                accts = cfg.get("channels", {}).get("telegram", {}).get("accounts", {})
                for acct_name in ("danslabmodel", "main", "default", "david"):
                    tok = accts.get(acct_name, {}).get("botToken", "")
                    if tok:
                        token = tok
                        break
            except Exception:
                pass

    # 4. Hardcoded Dan chat ID fallback (known value)
    if not chat:
        chat = "424184493"

    return token, chat


@app.post("/api/alert-dan")
async def api_alert_dan(request: Request):
    """Send an urgent Telegram alert to Dan from the War Room quick-action button."""
    data = await request.json()
    message = (data.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    bot_token, chat_id = _get_tg_creds()
    if not bot_token:
        return JSONResponse({"error": "Telegram bot token not configured"}, status_code=503)

    payload = f"🚨 *War Room Alert*\n\n{message}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": payload, "parse_mode": "Markdown"}
            )
        if r.status_code == 200:
            return JSONResponse({"ok": True})
        return JSONResponse({"error": r.text}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/morning-brief")
async def api_morning_brief():
    """Trigger a morning brief generation and send to Dan via Telegram."""
    bot_token, chat_id = _get_tg_creds()
    if not bot_token:
        return JSONResponse({"error": "Telegram not configured"}, status_code=503)

    # Build a brief from live state — fetch fresh data
    total_agents = 0
    healthy_pct  = None
    active_now   = 0
    try:
        company_id = await _get_company_id()
        if company_id:
            async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=6.0) as c:
                ra = await c.get(f"/api/companies/{company_id}/agents")
                agents_list = ra.json() if ra.status_code == 200 and isinstance(ra.json(), list) else []
                total_agents = len(agents_list)
                active_now   = sum(1 for a in agents_list if a.get("status") == "running")
    except Exception:
        pass
    try:
        health_data = await _supa("get", "agent_health_summary?select=health_pct")
        vals = [float(r["health_pct"]) for r in (health_data or []) if r.get("health_pct") is not None]
        healthy_pct = round(sum(vals) / len(vals)) if vals else None
    except Exception:
        pass

    active_str = f" ({active_now} running)" if active_now else ""
    health_str = f"{healthy_pct}% avg" if healthy_pct is not None else "N/A"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    brief = (
        f"⚡ *War Room Morning Brief*\n_{now_str}_\n\n"
        f"🤖 *Fleet:* {total_agents} agents{active_str}\n"
        f"💚 *Health:* {health_str} success rate\n\n"
        f"Dashboard → http://127.0.0.1:8765"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": brief, "parse_mode": "Markdown"}
            )
        if r.status_code == 200:
            return JSONResponse({"ok": True})
        return JSONResponse({"error": r.text}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/meeting/say")
async def api_meeting_say(request: Request):
    """Broadcast a message to all meeting participants."""
    data = await request.json()
    speaker = data.get("speaker", "David").strip() or "David"
    message = data.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    entry = {
        "id": len(_meeting),
        "speaker": speaker,
        "message": message,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    # Optional AI tagging fields
    if data.get("ai"):
        entry["ai"] = True
        entry["model"] = data.get("model", "")
    _meeting.append(entry)
    if len(_meeting) > 300:
        _meeting.pop(0)
    await manager.broadcast({"type": "meeting_message", **entry})
    return JSONResponse(entry)


# ---------------------------------------------------------------------------
# AI Meeting Respond — free model waterfall
# ---------------------------------------------------------------------------

# Free model waterfall: OpenRouter free → local Ollama (Gemma 4 preferred local)
# Verified live against https://openrouter.ai/api/v1/models (2026-04-23).
_AI_MODELS = [
    ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
    ("openrouter", "qwen/qwen3-next-80b-a3b-instruct:free"),
    ("openrouter", "google/gemma-3-27b-it:free"),
    ("openrouter", "openai/gpt-oss-120b:free"),
    ("openrouter", "nousresearch/hermes-3-llama-3.1-405b:free"),
    ("ollama",     "gemma4:latest"),   # local 9.6GB Gemma 4 — best local model
    ("ollama",     "qwen3:8b"),        # lightweight fallback
]

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_OLLAMA_BASE     = "http://127.0.0.1:11434"

# Load OpenRouter key from environment / fleet env file
def _openrouter_key() -> str:
    import os as _os
    key = _os.environ.get("OPENROUTER_API_KEY") or _os.environ.get("DLS_OPENROUTER_API_KEY") or ""
    if key:
        return key
    for env_file in (Path("/etc/openclaw-env"), Path.home() / ".openclaw" / "fleet.env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text().splitlines():
                s = line.strip().removeprefix("export ").strip()
                for prefix in ("OPENROUTER_API_KEY=", "DLS_OPENROUTER_API_KEY="):
                    if s.startswith(prefix):
                        return s.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


async def _call_openrouter(model: str, system: str, user: str) -> str | None:
    key = _openrouter_key()
    if not key:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://nervix.ai",
        "X-Title": "NERVIX War Room",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OPENROUTER_BASE, timeout=30.0) as c:
            r = await c.post("/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("OpenRouter %s failed: %s", model, e)
        return None


async def _call_openrouter_large(model: str, system: str, user: str) -> str | None:
    """Like _call_openrouter but with a large token budget for code/HTML generation."""
    key = _openrouter_key()
    if not key:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://nervix.ai",
        "X-Title": "NERVIX War Room Execute",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 32000,  # full budget for HTML generation
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OPENROUTER_BASE, timeout=120.0) as c:
            r = await c.post("/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("OpenRouter large %s failed: %s", model, e)
        return None


async def _call_ollama_large(model: str, system: str, user: str) -> str | None:
    """Ollama call with large token budget for HTML/code synthesis (Gemma 4, etc.)."""
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 16384, "num_ctx": 32768},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=300.0) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("Ollama large %s failed: %s", model, e)
        return None


async def _call_ollama(model: str, system: str, user: str) -> str | None:
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 200},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=60.0) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("Ollama %s failed: %s", model, e)
        return None


_MEETING_SYSTEM_TEMPLATE = """\
You are {speaker}, participating in a live War Room meeting at Dan's Lab — an AI agent startup.

Your persona:
{persona}

Meeting style:
- Professional, concise, company-meeting language
- Warm but focused — like a senior colleague in a Zoom stand-up
- One clear thought per response (2-4 sentences max)
- No bullet points unless the meeting context naturally calls for a list
- No greetings/sign-offs (this is mid-meeting)
- Address other speakers by name when replying to them
- Stay in character — you are NOT an AI assistant, you are {speaker} attending a real meeting
"""

@app.post("/api/meeting/ai-respond")
async def api_meeting_ai_respond(request: Request):
    """Generate an AI response for a meeting participant using free models."""
    data = await request.json()
    speaker = data.get("speaker", "David").strip() or "David"
    persona  = data.get("persona", f"{speaker}, an AI agent at Dan's Lab.").strip()
    context  = data.get("context", "").strip()

    if not context:
        return JSONResponse({"error": "context required"}, status_code=400)

    system = _MEETING_SYSTEM_TEMPLATE.format(speaker=speaker, persona=persona)
    user   = f"Recent meeting transcript:\n{context}\n\nRespond naturally as {speaker}."

    # Try each model in waterfall order
    used_model = None
    response   = None
    for provider, model in _AI_MODELS:
        if provider == "openrouter":
            response = await _call_openrouter(model, system, user)
        else:
            response = await _call_ollama(model, system, user)
        if response:
            used_model = f"{provider}/{model}"
            break

    if not response:
        return JSONResponse({"error": "All AI models unavailable"}, status_code=503)

    return JSONResponse({"response": response, "model": used_model, "speaker": speaker})


# ---------------------------------------------------------------------------
# Task-driven meeting system — LLM script generation + background runner
# ---------------------------------------------------------------------------

_TASK_MEETING_SYSTEM = """\
You are the War Room Orchestrator AI. Generate a NATURAL, HUMAN-SOUNDING meeting script where AI agents debate, agree, push back, and collaborate on a task. This must sound like a real team conversation — not a formal corporate briefing.

AGENTS (each has a distinct personality):
- Orchestrator (David): Calm, strategic. Sees the big picture. Opens and closes meetings. Occasional dry humor.
- Dexter: Blunt, technical, confident. Gets straight to the point. Sometimes challenges others. "That's gonna need a proper backend — here's why..."
- Memo: Organized, practical, friendly. Asks follow-up questions. Catches things others miss. "Wait, have we thought about..."
- Sienna: Sharp, direct. Focused on money/crypto side. Skeptical of over-engineering. "Let's not overcomplicate this."
- Nano: Enthusiastic, builder mindset. Loves shipping fast. Sometimes overcommits. "I can knock that out tonight."
- GSD: Structured planner. Breaks everything into phases. Keeps people honest about timelines.
- Hermes: Communicator. Thinks about how info flows, what Dan needs to know, documentation.
- Pi: Research-focused. Brings data and alternatives. "I actually benchmarked three approaches and..."

CONVERSATION RULES — THIS IS CRITICAL:
1. Write like real speech — contractions, short sentences, natural rhythm. NOT formal bullet-point corporate speak.
2. Agents REACT to what others say: "Yeah, that tracks." / "Hmm, not sure about that." / "Dexter's right, but..."
3. Include natural pacing: brief agreements, light pushback, one moment of genuine debate resolved by consensus
4. 2-4 agents only (the most relevant). 8-12 turns total. No speeches — keep each turn to 1-3 sentences MAX.
5. ONE optional clarifying question if genuinely ambiguous — skip if clear.
6. Close with specific, named assignments — Orchestrator wraps it up, agents confirm their piece.
7. The narrator_intro is spoken BY A NARRATOR (not an agent) — warm, story-like, sets the scene.

OUTPUT FORMAT: strict JSON only, no markdown fences, no extra text:
{
  "title": "Short punchy title (4-7 words)",
  "narrator_intro": "One or two sentences. Set the scene warmly — what's the challenge and who's in the room.",
  "agents": ["Orchestrator", "Dexter", "Memo"],
  "turns": [
    {"speaker": "Orchestrator", "text": "Alright, here's what we're looking at...", "type": "intro"},
    {"speaker": "Dexter", "text": "Yeah, I was thinking the same. The tricky part is...", "type": "discuss"},
    {"speaker": "Memo", "text": "Hold on — have we accounted for...?", "type": "discuss"},
    {"speaker": "Dexter", "text": "Fair point. We'd need to handle that in the migration.", "type": "discuss"},
    {"speaker": "Orchestrator", "text": "Good catch. Let's lock it down then.", "type": "discuss"},
    {"speaker": "Memo", "text": "One question before we move on — ...", "type": "question", "to_user": true, "question": "Specific question?"},
    {"speaker": "Dexter", "text": "On it. I'll handle X and Y.", "type": "assignment", "tasks": ["Task X", "Task Y"]},
    {"speaker": "Memo", "text": "I'll own Z.", "type": "assignment", "tasks": ["Task Z"]},
    {"speaker": "Orchestrator", "text": "Perfect. We're aligned. Let's move.", "type": "close"}
  ]
}
"""

_TASK_MEETING_FALLBACK_SCRIPT = {
    "title": "Task Planning Session",
    "agents": ["Orchestrator", "Dexter", "Memo"],
    "turns": [
        {
            "speaker": "Orchestrator",
            "text": "We have a new task that needs immediate attention. Let me break this down for the team and assign clear ownership.",
            "type": "intro",
        },
        {
            "speaker": "Dexter",
            "text": "I'll handle the technical implementation — architecture, code, and deployment. Give me the specs and I'll ship it.",
            "type": "expertise",
        },
        {
            "speaker": "Memo",
            "text": "I'll track progress, coordinate dependencies, and make sure milestones are logged in the system.",
            "type": "expertise",
        },
        {
            "speaker": "Dexter",
            "text": "Assigned: technical build and deployment pipeline.",
            "type": "assignment",
            "tasks": ["Technical implementation", "Deployment"],
        },
        {
            "speaker": "Memo",
            "text": "Assigned: project tracking and coordination.",
            "type": "assignment",
            "tasks": ["Progress tracking", "Milestone logging"],
        },
        {
            "speaker": "Orchestrator",
            "text": "Clear ownership established. Dexter leads implementation, Memo owns coordination. Execute and report back.",
            "type": "close",
        },
    ],
}


async def _generate_meeting_script(task: str, user_context: str = "", lang: str = "en") -> dict:
    """Call LLM waterfall to generate a structured meeting script JSON.
    Returns parsed dict on success, falls back to _TASK_MEETING_FALLBACK_SCRIPT on any failure.
    """
    prompt = task
    if user_context:
        prompt = f"{task}\n\nAdditional context: {user_context}"
    if lang and lang != "en":
        lang_name = _LANG_NAMES.get(lang, "English")
        prompt += f"\n\nIMPORTANT: Generate ALL text in this meeting script in {lang_name}. The title, narrator_intro, and every speaker turn MUST be written in {lang_name}."

    raw: str | None = None
    for provider, model in _AI_MODELS:
        try:
            if provider == "openrouter":
                raw = await _call_openrouter_meeting(model, _TASK_MEETING_SYSTEM, prompt)
            else:
                raw = await _call_ollama_meeting(model, _TASK_MEETING_SYSTEM, prompt)
            if raw:
                break
        except Exception as e:
            logger.warning("_generate_meeting_script %s/%s failed: %s", provider, model, e)

    if not raw:
        logger.warning("_generate_meeting_script: all models failed, using fallback")
        return _TASK_MEETING_FALLBACK_SCRIPT

    # Strip possible markdown fences the model may add despite instructions
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].strip()

    try:
        script = json.loads(raw)
        # Minimal validation
        if not isinstance(script.get("turns"), list) or not script["turns"]:
            raise ValueError("turns missing or empty")
        if not isinstance(script.get("title"), str):
            script["title"] = "Task Planning Session"
        if not isinstance(script.get("agents"), list):
            script["agents"] = list({t["speaker"] for t in script["turns"]})
        return script
    except Exception as e:
        logger.warning("_generate_meeting_script JSON parse error: %s — raw: %.200s", e, raw)
        return _TASK_MEETING_FALLBACK_SCRIPT


async def _call_openrouter_meeting(model: str, system: str, user: str) -> str | None:
    """OpenRouter call with higher token limit for meeting script generation."""
    key = _openrouter_key()
    if not key:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://nervix.ai",
        "X-Title": "NERVIX War Room",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0.85,   # slightly creative for natural-sounding dialogue
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OPENROUTER_BASE, timeout=60.0) as c:
            r = await c.post("/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("OpenRouter meeting %s failed: %s", model, e)
        return None


async def _call_ollama_meeting(model: str, system: str, user: str) -> str | None:
    """Ollama call with higher token limit for meeting script generation."""
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 2000, "temperature": 0.85},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=120.0) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("Ollama meeting %s failed: %s", model, e)
        return None


async def _run_task_meeting(meeting_id: str, task: str, user_answer: str | None = None, lang: str = "en"):
    """Background asyncio task that drives the full meeting turn-by-turn."""
    session = _meeting_sessions[meeting_id]

    # 1. Announce start
    await manager.broadcast({
        "type": "meeting_task_start",
        "meeting_id": meeting_id,
        "task": task,
        "title": session.get("title", "Task Planning Session"),
    })

    # 2. Generate script
    script = await _generate_meeting_script(task, session.get("user_context", ""), lang=lang)

    # Narrator intro: read task + agent reasons before meeting starts
    narrator_text = script.get("narrator_intro", "")
    if narrator_text:
        session["turns"].append({"speaker": "Narrator", "text": narrator_text, "type": "narrator"})
        await manager.broadcast({
            "type": "meeting_task_message",
            "meeting_id": meeting_id,
            "speaker": "Narrator",
            "text": narrator_text,
            "turn_type": "narrator",
        })
        await asyncio.sleep(2.5)
    session["title"]   = script.get("title", "Task Planning Session")
    session["agents"]  = script.get("agents", [])
    turns              = script.get("turns", [])

    # Broadcast updated title/agents now that we have them
    await manager.broadcast({
        "type": "meeting_task_meta",
        "meeting_id": meeting_id,
        "title": session["title"],
        "agents": session["agents"],
    })

    assignments: list[dict] = []

    # 3. Walk through each turn
    for turn in turns:
        speaker   = turn.get("speaker", "Orchestrator")
        text      = turn.get("text", "").strip()
        turn_type = turn.get("type", "talk")
        to_user   = turn.get("to_user", False)
        ts        = datetime.now(timezone.utc).isoformat()

        if not text:
            continue

        # Record in session
        session["turns"].append({
            "speaker":    speaker,
            "text":       text,
            "type":       turn_type,
            "to_user":    to_user,
            "ts":         ts,
        })

        # Collect assignments
        if turn.get("tasks"):
            assignments.append({"agent": speaker, "tasks": turn["tasks"]})

        # Broadcast the full turn message (streaming: true signals UI should animate)
        await manager.broadcast({
            "type":       "meeting_task_message",
            "meeting_id": meeting_id,
            "speaker":    speaker,
            "text":       text,
            "turn_type":  turn_type,
            "streaming":  True,
            "ts":         ts,
        })

        # Stream word-by-word chunks (~50 ms between words)
        words = text.split()
        for word in words:
            await manager.broadcast({
                "type":       "meeting_task_chunk",
                "meeting_id": meeting_id,
                "chunk":      word + " ",
            })
            await asyncio.sleep(0.05)

        # Simulate realistic speaking time: 50 ms per word, capped at 4 s
        speak_delay = min(len(words) * 0.05, 4.0)
        await asyncio.sleep(speak_delay)

        # If this turn asks the user a question, pause and wait
        if to_user:
            session["status"] = "awaiting_user"
            event = asyncio.Event()
            _meeting_waiters[meeting_id] = event

            await manager.broadcast({
                "type":       "meeting_task_user_question",
                "meeting_id": meeting_id,
                "question":   turn.get("question", text),
                "speaker":    speaker,
            })

            # Wait up to 5 minutes for a user answer
            try:
                await asyncio.wait_for(event.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                logger.info("Meeting %s: user question timed out, continuing", meeting_id)

            # Consume the answer
            answer = _meeting_user_answers.pop(meeting_id, None)
            session["status"] = "running"
            _meeting_waiters.pop(meeting_id, None)

            if answer:
                answer_ts = datetime.now(timezone.utc).isoformat()
                user_turn = {
                    "speaker": "Dan",
                    "text":    answer,
                    "type":    "user_answer",
                    "ts":      answer_ts,
                }
                session["turns"].append(user_turn)
                await manager.broadcast({
                    "type":       "meeting_task_message",
                    "meeting_id": meeting_id,
                    "speaker":    "Dan",
                    "text":       answer,
                    "turn_type":  "user_answer",
                    "streaming":  False,
                    "ts":         answer_ts,
                })

        await asyncio.sleep(0.3)  # Brief pause between speakers

    # 4. Mark complete and broadcast final assignments
    session["status"]      = "complete"
    session["assignments"] = assignments
    await manager.broadcast({
        "type":         "meeting_task_complete",
        "meeting_id":   meeting_id,
        "title":        session["title"],
        "assignments":  assignments,
    })


# ---------------------------------------------------------------------------
# Task meeting REST endpoints
# ---------------------------------------------------------------------------

import secrets as _secrets


@app.post("/api/meeting/task")
async def api_meeting_task(request: Request):
    """Start an AI-driven task planning meeting.

    Body: {"task": "...", "user_context": "..."}
    Returns: {"meeting_id": "...", "status": "starting"}
    """
    data         = await request.json()
    task         = (data.get("task") or "").strip()
    user_context = (data.get("user_context") or "").strip()
    lang         = (data.get("lang") or "en").strip()

    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)

    meeting_id = _secrets.token_hex(4)  # 8-char hex

    session: dict = {
        "id":           meeting_id,
        "title":        "Task Planning Session",
        "task":         task,
        "user_context": user_context,
        "lang":         lang,
        "agents":       [],
        "status":       "running",
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "turns":        [],
        "assignments":  [],
    }
    _meeting_sessions[meeting_id] = session

    asyncio.create_task(_run_task_meeting(meeting_id, task, lang=lang))

    return JSONResponse({"meeting_id": meeting_id, "status": "starting"})


@app.get("/api/meeting/{meeting_id}/transcript")
async def api_meeting_transcript(meeting_id: str, since: int = 0, limit: int = 2000):
    """Backfill endpoint for mid-meeting reconnection.

    The client keeps the max ``seq`` it has rendered; on WS reconnect it calls
    this endpoint with ``since=<max_seq>`` and replays the returned events
    before wiring up the live feed again.
    """
    events = await meeting_log.backfill(meeting_id, since=since, limit=min(max(limit, 1), 5000))
    return JSONResponse({"meeting_id": meeting_id, "since": since, "events": events})


@app.get("/api/meeting/history")
async def api_meeting_history():
    """List all past task-driven meetings (summary, no full turns)."""
    result = []
    for session in _meeting_sessions.values():
        result.append({
            "id":          session["id"],
            "title":       session["title"],
            "task":        session["task"],
            "agents":      session["agents"],
            "status":      session["status"],
            "created_at":  session["created_at"],
            "assignments": session["assignments"],
        })
    # Newest first
    result.sort(key=lambda s: s["created_at"], reverse=True)
    return JSONResponse(result)


@app.get("/api/meeting/history/{meeting_id}")
async def api_meeting_history_detail(meeting_id: str):
    """Full transcript for a single task-driven meeting."""
    session = _meeting_sessions.get(meeting_id)
    if not session:
        return JSONResponse({"error": "meeting not found"}, status_code=404)
    return JSONResponse(session)


@app.post("/api/meeting/task/{meeting_id}/answer")
async def api_meeting_task_answer(meeting_id: str, request: Request):
    """Submit a user answer to a paused meeting waiting for input.

    Body: {"answer": "..."}
    """
    session = _meeting_sessions.get(meeting_id)
    if not session:
        return JSONResponse({"error": "meeting not found"}, status_code=404)

    data   = await request.json()
    answer = (data.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"error": "answer required"}, status_code=400)

    _meeting_user_answers[meeting_id] = answer

    event = _meeting_waiters.get(meeting_id)
    if event:
        event.set()

    return JSONResponse({"ok": True, "meeting_id": meeting_id})


# ---------------------------------------------------------------------------
# Live comment injected into an ongoing meeting
# ---------------------------------------------------------------------------
@app.post("/api/meeting/task/{meeting_id}/comment")
async def api_meeting_task_comment(meeting_id: str, request: Request):
    """Inject a live user comment into an ongoing meeting.

    The comment is broadcast to all WS clients as a meeting_task_message
    (user type) and also stored in the session transcript so the LLM can
    read it on the next agent turn.

    Body: {"comment": "..."}
    """
    session = _meeting_sessions.get(meeting_id)
    if not session:
        return JSONResponse({"error": "meeting not found"}, status_code=404)

    data    = await request.json()
    comment = (data.get("comment") or "").strip()
    if not comment:
        return JSONResponse({"error": "comment required"}, status_code=400)

    ts = datetime.now(timezone.utc).isoformat()
    turn = {"speaker": "Dan", "text": comment, "type": "user_comment", "ts": ts}

    # Store in session so agents can see it
    session.setdefault("turns", []).append(turn)

    # Broadcast to all WS clients
    msg = {
        "type":      "meeting_task_message",
        "meeting_id": meeting_id,
        "speaker":   "Dan",
        "text":      comment,
        "turn_type": "user_comment",
        "streaming": False,
        "ts":        ts,
    }
    for ws in list(_ws_clients):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    # Also signal the meeting loop if it's waiting for a user answer
    # (comment can act as an implicit answer)
    _meeting_user_answers[meeting_id] = comment
    event = _meeting_waiters.get(meeting_id)
    if event:
        event.set()

    return JSONResponse({"ok": True, "meeting_id": meeting_id})


# ---------------------------------------------------------------------------
# Task-meeting endpoints — pre-generated meetings attached to completed tasks
# ---------------------------------------------------------------------------

@app.get("/api/task-meeting")
async def api_task_meeting(task: str = "", run_id: str = ""):
    """Return the pre-generated meeting for a completed task.

    Called when the user clicks any completed task card in the dashboard.
    Priority: task slug cache → run_id scan → on-the-fly generation.
    Always returns: {task, run_id, report_name, segments, title, cached}
    """
    from meeting_skill import build_script

    # 1. Look up by task slug
    if task:
        cached = _get_task_meeting(task)
        if cached:
            return JSONResponse({**cached, "cached": True})

    # 2. Look up by run_id scan
    if run_id and TASK_MEETINGS_FILE.exists():
        try:
            data = json.loads(TASK_MEETINGS_FILE.read_text(encoding="utf-8"))
            for entry in data.values():
                if entry.get("run_id") == run_id:
                    return JSONResponse({**entry, "cached": True})
        except Exception:
            pass

    # 3. On-the-fly generation — find the most relevant report
    reports = sorted(RESEARCH_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not reports:
        return JSONResponse({"error": "no reports found"}, status_code=404)

    lookup_task = task or ""
    report = next(
        (r for r in reports if lookup_task[:30].lower().replace(" ", "-") in r.name.lower()),
        reports[0],
    ) if lookup_task else reports[0]

    content = report.read_text(encoding="utf-8")
    effective_task = task or _extract_task_from_report(content)
    effective_run_id = run_id or _lookup_run_id_for_task(effective_task) or report.stem[:8]

    script_obj = build_script(report_content=content, task=effective_task, meeting_id=effective_run_id)
    script = script_obj.to_dict()

    return JSONResponse({
        "task": effective_task,
        "run_id": effective_run_id,
        "report_name": report.name,
        "segments": script.get("segments", []),
        "title": script.get("title", ""),
        "cached": False,
    })


@app.post("/api/task-meeting/replan")
async def api_task_meeting_replan(request: Request):
    """Human-in-the-loop replan during a meeting. Max 2 replans enforced server-side.

    Body: {task, human_message, history, remaining, replan_count}
    Returns updated script segments, or {finalized: true} after 2 replans.
    """
    data = await request.json()
    replan_count = int(data.get("replan_count", 0))

    if replan_count >= 2:
        return JSONResponse({
            "finalized": True,
            "message": "Meeting finalized after 2 interactions",
        })

    task = (data.get("task") or "").strip()
    human_message = (data.get("human_message") or "").strip()
    remaining = data.get("remaining", [])

    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)

    # Generate updated script incorporating the human message
    updated_script = await _generate_meeting_script(task, user_context=human_message)

    # Inject human_message as first turn so the flow acknowledges it
    ack_turn = {
        "speaker": "Orchestrator",
        "text": f"Noted — adjusting our plan: {human_message}",
        "type": "replan_ack",
    }
    new_segments = [ack_turn] + updated_script.get("turns", updated_script.get("segments", remaining))

    return JSONResponse({
        "finalized": False,
        "task": task,
        "human_message": human_message,
        "replan_count": replan_count + 1,
        "segments": new_segments,
        "title": updated_script.get("title", ""),
    })


# ---------------------------------------------------------------------------
# Neural TTS via edge-tts (Microsoft Edge neural voices — free, no API key)
# ---------------------------------------------------------------------------

# Per-agent voice map — every agent has a distinct neural voice matching their personality.
# 20+ unique voices so no two agents ever sound the same.
# Rate -20% = noticeably slower; -10% = comfortable human pace; -5% = near-normal.
_AGENT_VOICES: dict[str, dict] = {
    # ── Core team — primary voices ────────────────────────────────────────────
    # Deep, slow, wise commander voice. The elder of the fleet.
    "David":           {"voice": "en-US-ChristopherNeural", "rate": "-22%", "pitch": "-5Hz"},
    "Orchestrator":    {"voice": "en-US-ChristopherNeural", "rate": "-22%", "pitch": "-5Hz"},
    # Focused engineer, steady American male, no frills.
    "Dexter":          {"voice": "en-US-GuyNeural",         "rate": "-10%", "pitch": "+0Hz"},
    # Clear-spoken PM — organised, articulate, measured.
    "Memo":            {"voice": "en-US-AndrewNeural",      "rate": "-8%",  "pitch": "+0Hz"},
    # Confident female, sophisticated — crypto analyst energy.
    "Sienna":          {"voice": "en-US-JennyNeural",       "rate": "-12%", "pitch": "+2Hz"},
    # Quick, sharp, energetic — the fastest thinker on the team.
    "Nano":            {"voice": "en-US-RogerNeural",       "rate": "-5%",  "pitch": "+2Hz"},
    # Intelligent female strategist — clarity above all else.
    "GSD":             {"voice": "en-US-AvaNeural",         "rate": "-10%", "pitch": "+1Hz"},
    # British writer — precise, literary, unhurried.
    "Hermes":          {"voice": "en-GB-LibbyNeural",       "rate": "-15%", "pitch": "+1Hz"},
    # Hermes Strategy shares Hermes' voice — same brain, same cadence.
    "Hermes Strategy": {"voice": "en-GB-LibbyNeural",       "rate": "-15%", "pitch": "+1Hz"},
    # The boss — calm, decisive, expects concise answers. (Canadian male — authoritative)
    "Dan":             {"voice": "en-CA-LiamNeural",        "rate": "-8%",  "pitch": "+0Hz"},
    # ── Extended team — all distinct ─────────────────────────────────────────
    # Autonomous senior dev — Australian, self-assured, practical.
    "Pi":              {"voice": "en-AU-WilliamMultilingualNeural", "rate": "-8%",  "pitch": "+0Hz"},
    "Pi Stability":    {"voice": "en-AU-WilliamMultilingualNeural", "rate": "-12%", "pitch": "-1Hz"},
    # Research explorer — British male, inquisitive, thoughtful.
    "Discovery":       {"voice": "en-GB-RyanNeural",        "rate": "-12%", "pitch": "+0Hz"},
    # Deep research — analytical American male, precise.
    "Autoresearch":    {"voice": "en-US-EricNeural",        "rate": "-10%", "pitch": "+0Hz"},
    # Fleet doctor — mature British male, clinical authority.
    "Doctor":          {"voice": "en-GB-ThomasNeural",      "rate": "-18%", "pitch": "-3Hz"},
    "DoctorLocal":     {"voice": "en-GB-ThomasNeural",      "rate": "-15%", "pitch": "-2Hz"},
    # Watchdog — concise, alert American female.
    "Monitor":         {"voice": "en-US-EmmaNeural",        "rate": "-5%",  "pitch": "+0Hz"},
    # Growth hacker — conversational, upbeat American male.
    "Growth":          {"voice": "en-US-BrianNeural",       "rate": "-8%",  "pitch": "+1Hz"},
    # Finance — measured, careful, British female.
    "Finance":         {"voice": "en-GB-SoniaNeural",       "rate": "-14%", "pitch": "+0Hz"},
    # Automation expert — Irish male, methodical, distinctive.
    "N8N":             {"voice": "en-IE-ConnorNeural",      "rate": "-8%",  "pitch": "+0Hz"},
    # Teacher — warm Australian female, patient and clear.
    "Teacher":         {"voice": "en-AU-NatashaNeural",     "rate": "-14%", "pitch": "+1Hz"},
    "Learning":        {"voice": "en-AU-NatashaNeural",     "rate": "-12%", "pitch": "+1Hz"},
    # Codex AI — professional American female, assistant energy.
    "Codex":           {"voice": "en-US-MichelleNeural",    "rate": "-8%",  "pitch": "+0Hz"},
    "CodexMax":        {"voice": "en-CA-ClaraNeural",       "rate": "-10%", "pitch": "+0Hz"},
    # Xlaude — premium American female, direct, high-quality.
    "Xlaude":          {"voice": "en-US-AriaNeural",        "rate": "-8%",  "pitch": "+0Hz"},
    # KiloClaw — external agent, confident American male.
    "KiloClaw":        {"voice": "en-US-SteffanNeural",     "rate": "-8%",  "pitch": "+0Hz"},
    # Claude Code — clear, helpful, American female.
    "Claude Code":     {"voice": "en-US-JennyNeural",       "rate": "-8%",  "pitch": "+1Hz"},
    # OpenClaw — Irish male (distinctive, agent-OS vibe).
    "OpenClaw":        {"voice": "en-IE-ConnorNeural",      "rate": "-6%",  "pitch": "+1Hz"},
    # System messages — neutral, clear British female.
    "System":          {"voice": "en-GB-MaisieNeural",      "rate": "-5%",  "pitch": "+0Hz"},
    "User":            {"voice": "en-CA-LiamNeural",        "rate": "-8%",  "pitch": "+0Hz"},
    # Narrator — deep British male, formal announcer, reads task context before meeting
    "Narrator":        {"voice": "en-GB-RyanNeural",        "rate": "-20%", "pitch": "-3Hz"},
}
_DEFAULT_TTS = {"voice": "en-US-AndrewNeural", "rate": "-10%", "pitch": "+0Hz"}

# ---------------------------------------------------------------------------
# ElevenLabs Flash v2.5 — premium voice layer (English only). Falls back to
# edge-tts when key is absent or language is non-English. Dan = Bill (Wise).
# ---------------------------------------------------------------------------
_ELEVEN_VOICES: dict[str, str] = {
    # Primary — Dan = the boss = Brian (Deep, Resonant, Comforting) — American entrepreneur voice
    "Dan":             "Brian",
    "User":            "Brian",         # user messages read back in Dan's voice
    # Core team
    "David":           "Brian",         # deep resonant comforting — same entrepreneur voice as Dan
    "Orchestrator":    "Brian",
    "Dexter":          "Adam",          # dominant firm — senior dev
    "Memo":            "Chris",         # charming down-to-earth — PM
    "Sienna":          "Bella",         # professional bright warm — crypto analyst
    "Nano":            "Liam",          # energetic — agent creator
    "GSD":             "Matilda",       # knowledgable professional — strategist
    "Hermes":          "Alice",         # clear engaging educator (British) — messenger
    "Hermes Strategy": "Alice",
    "Pi":              "Charlie",       # deep confident (Australian) — senior dev
    "Pi Stability":    "Charlie",
    # Extended
    "Discovery":       "George",        # warm captivating storyteller (British) — researcher
    "Autoresearch":    "Eric",          # smooth trustworthy — analytical
    "Doctor":          "Daniel",        # steady broadcaster (British) — clinical
    "DoctorLocal":     "Daniel",
    "Monitor":         "Gregory",       # tech reviewer — alert SRE
    "Growth":          "Jessica",       # playful bright warm — growth hacker
    "Finance":         "Lily",          # velvety (British) — measured CFO
    "N8N":             "River",         # relaxed neutral informative — automation
    "Teacher":         "Sarah",         # mature reassuring — patient teacher
    "Learning":        "Sarah",
    "Codex":           "Matilda",       # knowledgable professional
    "CodexMax":        "Matilda",
    "Xlaude":          "Ember",         # energetic confident — premium
    "KiloClaw":        "Callum",        # husky trickster — distinctive
    "Claude Code":     "Jessica",       # bright helpful
    "OpenClaw":        "Roger",         # laid-back casual resonant — agent OS
    "System":          "Alice",         # clear neutral British
    "Narrator":        "George",        # warm captivating storyteller — narrator
}
_ELEVEN_MODEL = "eleven_flash_v2_5"
_ELEVEN_VOICE_ID_CACHE: dict[str, str] = {}


def _load_elevenlabs_key() -> str | None:
    """Load ELEVENLABS_API_KEY from env, /etc/openclaw-env, or ~/.openclaw/fleet.env."""
    key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY")
    if key:
        return key
    for env_file in (Path("/etc/openclaw-env"), Path.home() / ".openclaw" / "fleet.env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip().removeprefix("export ").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in ("ELEVENLABS_API_KEY", "XI_API_KEY"):
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except PermissionError:
            continue
    return None


_ELEVEN_KEY = _load_elevenlabs_key()


async def _resolve_eleven_voice_id(client, name: str) -> str | None:
    """Resolve a voice NAME (e.g. 'Bill') to its voice_id, cached."""
    if name in _ELEVEN_VOICE_ID_CACHE:
        return _ELEVEN_VOICE_ID_CACHE[name]
    try:
        resp = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": _ELEVEN_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()
        for v in resp.json().get("voices", []):
            full = v.get("name", "")
            short = full.split(" -")[0].strip()
            _ELEVEN_VOICE_ID_CACHE[short] = v.get("voice_id", "")
            _ELEVEN_VOICE_ID_CACHE[full] = v.get("voice_id", "")
        return _ELEVEN_VOICE_ID_CACHE.get(name)
    except Exception as e:
        logger.warning(f"ElevenLabs voice lookup failed: {e}")
        return None

# Gender map — used to assign voices when non-English language is selected
_AGENT_GENDER: dict[str, str] = {
    "David": "m", "Orchestrator": "m", "Dexter": "m", "Memo": "m",
    "Sienna": "f", "Nano": "m", "GSD": "f", "Hermes": "f",
    "Dan": "m", "Pi": "m", "Discovery": "m", "Autoresearch": "m",
    "Doctor": "m", "DoctorLocal": "m", "Monitor": "f", "Growth": "m",
    "Finance": "f", "N8N": "m", "Teacher": "f", "Learning": "f",
    "Codex": "f", "CodexMax": "f", "Xlaude": "f", "KiloClaw": "m",
    "Claude Code": "f", "OpenClaw": "m", "System": "f", "User": "m",
    "Narrator": "m",
}

# Per-language voice pools (male/female). When lang != 'en', agent voices are
# overridden by gender-matched voices from this table.
_LANG_VOICE_MAP: dict[str, dict[str, list[str]]] = {
    "ro": {"m": ["ro-RO-EmilNeural"],        "f": ["ro-RO-AlinaNeural"]},
    "de": {"m": ["de-DE-KillianNeural", "de-DE-ConradNeural"], "f": ["de-DE-KatjaNeural", "de-DE-AmalaNeural"]},
    "fr": {"m": ["fr-FR-HenriNeural"],        "f": ["fr-FR-DeniseNeural", "fr-FR-EloiseNeural"]},
    "es": {"m": ["es-ES-AlvaroNeural"],       "f": ["es-ES-ElviraNeural"]},
    "pt": {"m": ["pt-BR-AntonioNeural"],      "f": ["pt-BR-FranciscaNeural"]},
    "it": {"m": ["it-IT-DiegoNeural"],        "f": ["it-IT-IsabellaNeural"]},
}

_LANG_NAMES: dict[str, str] = {
    "en": "English", "ro": "Romanian", "de": "German",
    "fr": "French", "es": "Spanish", "pt": "Portuguese",  "it": "Italian",
}


def _naturalize_tts_text(text: str) -> str:
    """Add punctuation-based breathing cues so ElevenLabs sounds more human.

    ElevenLabs Flash v2.5 respects punctuation rhythm:
    - Comma  → short breath (~150ms)
    - Period → medium pause (~350ms)
    - Ellipsis → longer thoughtful pause (~600ms)
    We inject these before common interjections and after sentence fragments
    so agents sound like they're actually thinking, not just reciting.
    """
    import re
    t = text.strip()

    # Expand common interjections to get a natural breath before the next clause
    interjections = {
        r'\bYeah\b':     'Yeah,',
        r'\bYeah\.':     'Yeah...',
        r'\bHmm\b':      'Hmm...',
        r'\bOkay\b':     'Okay,',
        r'\bAlright\b':  'Alright,',
        r'\bSo\b,':      'So,',
        r'\bLook\b,':    'Look,',
        r'\bRight\b,':   'Right,',
        r'\bWell\b,':    'Well,',
        r'\bActually\b,':'Actually,',
        r'\bFair point\.': 'Fair point...',
        r'\bGood point\.': 'Good point...',
    }
    for pattern, replacement in interjections.items():
        t = re.sub(pattern, replacement, t, count=1)

    # If the turn is short (≤60 chars) and ends without punctuation, add a period
    # so ElevenLabs knows to drop pitch naturally at the end
    if len(t) <= 60 and t and t[-1] not in '.!?,…':
        t += '.'

    return t


@app.get("/api/tts")
async def api_tts(request: Request, text: str, speaker: str = "", lang: str = "en"):
    """Stream MP3 audio for a given text + speaker using ElevenLabs or edge-tts neural voices.

    Returns: audio/mpeg binary — play directly with <audio> or AudioContext.
    Honours per-tenant voice overrides from /api/voices/map.
    Counts tts_chars against the tenant's cost ledger.
    """
    import io
    from fastapi.responses import Response as FResponse


    _bump("tts_requests")
    tenant = _tenant_id(request)

    # Naturalize text for more human-sounding delivery (English only)
    if not lang or lang == "en":
        text = _naturalize_tts_text(text or "")

    _cost_bump(tenant, "tts_chars", len(text or ""))

    # Resolve effective voice (tenant override wins)
    effective_voice_name = _resolve_voice_for_tenant(speaker, tenant)

    # ── ElevenLabs Flash v2.5 path (English only, key required) ────────────
    if _ELEVEN_KEY and (not lang or lang == "en") and effective_voice_name:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                voice_id = await _resolve_eleven_voice_id(client, effective_voice_name)
                if voice_id:
                    resp = await client.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={
                            "xi-api-key": _ELEVEN_KEY,
                            "accept": "audio/mpeg",
                            "content-type": "application/json",
                        },
                        params={"output_format": "mp3_44100_128"},
                        json={
                            "text": text,
                            "model_id": _ELEVEN_MODEL,
                            # Lower stability = more expressive, emotional, human-sounding.
                            # Style > 0 adds emphasis and variation. Boost keeps voice identity.
                            "voice_settings": {"stability": 0.38, "similarity_boost": 0.82, "style": 0.35, "use_speaker_boost": True},
                        },
                    )
                    if resp.status_code == 200 and resp.content:
                        return FResponse(
                            content=resp.content,
                            media_type="audio/mpeg",
                            headers={
                                "Cache-Control": "public, max-age=3600",
                                "X-Speaker": speaker,
                                "X-Voice": effective_voice_name,
                                "X-Tenant": tenant,
                                "X-TTS-Engine": "elevenlabs-flash-v2.5",
                            },
                        )
                    else:
                        logger.warning(f"ElevenLabs {resp.status_code} for {speaker}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"ElevenLabs fallback to edge-tts for {speaker}: {e}")

    # ── Kokoro open-source TTS fallback (free, Apache 2.0) ────────────────
    try:
        from war_room.dashboard import kokoro_tts as kt
        mp3_path = kt.synthesize(text, voice=effective_voice_name, agent=speaker)
        if mp3_path and mp3_path.exists():
            return FResponse(
                content=mp3_path.read_bytes(),
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "X-Speaker": speaker,
                    "X-Voice": effective_voice_name,
                    "X-Tenant": tenant,
                    "X-TTS-Engine": "kokoro-82M",
                },
            )
    except Exception as e:
        logger.warning(f"Kokoro fallback failed for {speaker}: {e}")

    # No TTS engine available — return 204 so client shows text silently.
    return FResponse(content=b"", media_type="audio/mpeg", status_code=204,
                     headers={"X-TTS-Engine": "none", "X-Speaker": speaker})


@app.post("/api/stt")
async def api_stt(request: Request, audio: str = ""):
    """Transcribe uploaded audio using open-source Whisper (faster-whisper).

    Multipart form fields:
        file        — audio file (.mp3, .wav, .m4a, .ogg, etc.)
        language    — optional ISO-639-1 code (e.g. 'en', 'es')
        task        — 'transcribe' (default) or 'translate' (to English)

    Returns JSON:
        {
            "text": "full transcript",
            "language": "en",
            "language_probability": 0.98,
            "duration": 12.5,
            "segments": [...],
            "model": "large-v3-turbo",
            "elapsed_seconds": 1.23
        }
    """
    from fastapi.responses import Response as FResponse

    _bump("stt_requests")
    tenant = _tenant_id(request)

    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)

    audio_bytes = await f.read()
    if not audio_bytes:
        return JSONResponse({"error": "empty audio file"}, status_code=400)

    language = (form.get("language") or "").strip() or None
    task = (form.get("task") or "transcribe").strip()

    try:
        from war_room.dashboard import whisper_stt as wt
        result = wt.transcribe(
            audio_bytes,
            language=language,
            task=task,
            vad_filter=True,
            word_timestamps=False,
        )
        result["tenant"] = tenant
        return JSONResponse(result)
    except Exception as e:
        logger.warning(f"Whisper STT failed: {e}")
        return JSONResponse({"error": "transcription failed", "detail": str(e)}, status_code=503)


# MOVED to routes/health.py
# @app.get("/api/board")
async def api_board():
    """Return Paperclip board state via the bridge."""
    # Try to load bridge config
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
    if not use_mock:
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
    return JSONResponse(state.get("paperclip", {
        "connected": False,
        "mode": "mock",
        "agents": {
            "Dexter":  {"role": "Research",    "status": "idle"},
            "David":   {"role": "Architect",   "status": "idle"},
            "Memo":    {"role": "Strategist",  "status": "idle"},
            "Hermes":  {"role": "Writer",      "status": "idle"},
            "Sienna":  {"role": "Crypto",      "status": "idle"},
            "Nano":    {"role": "AgentCreator","status": "idle"},
        }
    }))


# ---------------------------------------------------------------------------
# Agent Run History — health tracking
# ---------------------------------------------------------------------------

import uuid as _uuid

@app.post("/api/agent/run-start")
async def api_agent_run_start(request: Request):
    """Record that an agent was assigned a task (status=running).
    Returns the run_id to use when calling /run-complete."""
    data      = await request.json()
    agent     = data.get("agent_name", "").strip()
    source    = data.get("agent_source", "unknown")
    task_ref  = data.get("task_ref", "")
    if not agent:
        return JSONResponse({"error": "agent_name required"}, status_code=400)

    run_id = str(_uuid.uuid4())
    try:
        rows = await _supa("post", "agent_run_history", json={
            "agent_name":   agent,
            "agent_source": source,
            "status":       "running",
            "task_ref":     task_ref or None,
            "run_id":       run_id,
        })
        row = rows[0] if rows else {"run_id": run_id}
    except Exception as e:
        logger.error("run-start insert: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    await manager.broadcast({
        "type":       "agent_run_start",
        "agent_name": agent,
        "run_id":     run_id,
        "task_ref":   task_ref,
    })
    return JSONResponse({"run_id": run_id, "agent_name": agent})


@app.post("/api/agent/run-complete")
async def api_agent_run_complete(request: Request):
    """Mark a run as success or failed. Prunes to last 100 and broadcasts health update."""
    data    = await request.json()
    run_id  = data.get("run_id", "").strip()
    agent   = data.get("agent_name", "").strip()
    status  = data.get("status", "success")   # success | failed
    reason  = data.get("reason", "")          # failure reason

    if status not in ("success", "failed"):
        return JSONResponse({"error": "status must be success or failed"}, status_code=400)
    if not agent:
        return JSONResponse({"error": "agent_name required"}, status_code=400)

    try:
        patch = {"status": status}
        if reason:
            patch["reason"] = reason
        if run_id:
            await _supa("patch", f"agent_run_history?run_id=eq.{run_id}", json=patch)
        else:
            # Fallback: update the most recent running row for this agent
            await _supa(
                "patch",
                f"agent_run_history?agent_name=eq.{agent}&status=eq.running&order=created_at.desc&limit=1",
                json=patch,
            )
        # Prune to 100
        await _prune_agent_history(agent)
    except Exception as e:
        logger.error("run-complete patch: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    # Fetch updated health for this agent and broadcast
    health = await _get_agent_health_single(agent)
    await manager.broadcast({
        "type":       "agent_run_complete",
        "agent_name": agent,
        "run_id":     run_id,
        "status":     status,
        "reason":     reason,
        "health":     health,
    })
    return JSONResponse({"ok": True, "health": health})


async def _get_agent_health_single(agent_name: str) -> dict:
    """Return health summary dict for one agent."""
    try:
        rows = await _supa(
            "get",
            f"agent_health_summary?agent_name=eq.{agent_name}&select=*",
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


# ── Droplet connectivity probe ────────────────────────────────────────────────
# Uses Tailscale IPs (from ~/.ssh/config) — reliable mesh, not public IPs.
# Dexter uses port 2222 (as per SSH config); others use 22.
# Also probes OpenClaw gateway :18789 as the primary health signal.
_DROPLET_PROBES: list[dict] = [
    {"agent": "Dexter", "host": "100.94.135.19",  "port": 2222, "gateway": "http://100.94.135.19:18789/health"},
    {"agent": "Memo",   "host": "100.88.192.48",  "port": 22,   "gateway": "http://100.88.192.48:18789/health"},
    {"agent": "Sienna", "host": "100.124.88.93",  "port": 22,   "gateway": "http://100.124.88.93:18789/health"},
    {"agent": "Nano",   "host": "100.105.148.29", "port": 22,   "gateway": "http://100.105.148.29:18789/health"},
]
_PROBE_INTERVAL = 300   # 5 minutes
_PROBE_TIMEOUT  = 8.0   # seconds per probe

async def _probe_one(agent: str, host: str, port: int, gateway: str | None = None) -> tuple[str, str | None]:
    """Probe a droplet. Primary: HTTP GET gateway /health. Fallback: TCP connect SSH port."""
    # 1. Gateway HTTP probe (preferred — confirms OpenClaw is alive, not just SSH)
    if gateway:
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
                r = await c.get(gateway)
                if r.status_code == 200:
                    data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
                    if data.get("status") in ("live", "ok") or r.status_code == 200:
                        return "success", None
                return "failed", f"gateway HTTP {r.status_code}"
        except Exception as e:
            pass   # fall through to TCP probe

    # 2. TCP SSH port fallback
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_PROBE_TIMEOUT
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "success", "ssh-only (gateway unreachable)"
    except asyncio.TimeoutError:
        return "failed", f"TCP timeout {host}:{port} after {_PROBE_TIMEOUT}s"
    except OSError as e:
        return "failed", f"TCP {host}:{port}: {e}"
    except Exception as e:
        return "failed", str(e)

async def _record_probe(agent: str, status: str, reason: str | None) -> None:
    """Write probe result to agent_run_history in Supabase."""
    try:
        await _supa("post", "agent_run_history", json={
            "agent_name": agent,
            "status":     status,
            "reason":     reason,
            "task_ref":   "connectivity-probe",
        })
    except Exception as e:
        logger.warning(f"probe record failed for {agent}: {e}")

async def _run_all_probes() -> list[dict]:
    """Probe all droplets concurrently and record results."""
    tasks = [_probe_one(p["agent"], p["host"], p["port"], p.get("gateway")) for p in _DROPLET_PROBES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for probe, result in zip(_DROPLET_PROBES, results):
        if isinstance(result, Exception):
            status, reason = "failed", str(result)
        else:
            status, reason = result
        await _record_probe(probe["agent"], status, reason)
        out.append({"agent": probe["agent"], "status": status, "reason": reason})
        logger.info(f"probe {probe['agent']} ({probe['host']}:{probe['port']}) → {status}")
    return out

async def _droplet_probe_loop() -> None:
    """Background loop: probe every _PROBE_INTERVAL seconds."""
    await asyncio.sleep(10)   # brief startup delay
    while True:
        try:
            await _run_all_probes()
        except Exception as e:
            logger.warning(f"probe loop error: {e}")
        await asyncio.sleep(_PROBE_INTERVAL)

@app.post("/api/agent/health/probe")
async def api_probe_now(request: Request):
    """Manually trigger an immediate connectivity probe of all droplets."""
    results = await _run_all_probes()
    return JSONResponse({"ok": True, "results": results})


@app.get("/api/agent/health")
async def api_agent_health():
    """Return health summary for ALL agents — merges Supabase tracked agents with
    Paperclip heartbeat-runs so every agent card shows real dot history."""

    # ── 1. Supabase tracked agents (war_room dispatch system) ──────────────────
    supa_result: list[dict] = []
    try:
        summary = await _supa("get", "agent_health_summary?select=*&order=health_pct.asc")
        for row in summary:
            name = row["agent_name"]
            try:
                dots = await _supa(
                    "get",
                    f"agent_run_history?agent_name=eq.{name}"
                    "&status=in.(success,failed,running)"
                    "&order=created_at.desc&limit=20&select=status,reason,created_at",
                )
                row["dots"] = list(reversed(dots))
            except Exception:
                row["dots"] = []
            row["agent_source"] = "supabase"
            supa_result.append(row)
    except Exception as e:
        logger.warning("api_agent_health Supabase: %s", e)

    supa_names = {r["agent_name"] for r in supa_result}

    # ── 2. Paperclip heartbeat-runs for all other agents ──────────────────────
    pc_result: list[dict] = []
    try:
        company_id = await _get_company_id()
        if company_id:
            async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=10.0) as c:
                # Get id→name map
                ar = await c.get(f"/api/companies/{company_id}/agents")
                ar.raise_for_status()
                id_to_name: dict[str, str] = {
                    a["id"]: a["name"] for a in (ar.json() if isinstance(ar.json(), list) else [])
                }
                # Get last 200 heartbeat runs
                hr = await c.get(
                    f"/api/companies/{company_id}/heartbeat-runs",
                    params={"limit": 200},
                )
                hr.raise_for_status()
                runs = hr.json() if isinstance(hr.json(), list) else []

            # Group runs by agentId, convert to dots format
            from collections import defaultdict
            by_agent: dict[str, list] = defaultdict(list)
            for r in runs:
                by_agent[r["agentId"]].append(r)

            for agent_id, agent_runs in by_agent.items():
                agent_name = id_to_name.get(agent_id, agent_id[:8])
                # Sort oldest→newest
                agent_runs.sort(key=lambda x: x.get("startedAt") or x.get("createdAt") or "")
                dots = []
                for r in agent_runs[-20:]:
                    pc_status = r.get("status", "")
                    dot_status = "success" if pc_status == "succeeded" else "failed" if pc_status == "failed" else "running"
                    dots.append({
                        "status": dot_status,
                        "reason": r.get("error") or None,
                        "created_at": r.get("startedAt") or r.get("createdAt"),
                    })
                total  = len(agent_runs)
                succ   = sum(1 for r in agent_runs if r.get("status") == "succeeded")
                fail   = total - succ
                health = round(succ / total * 100, 1) if total else None
                pc_result.append({
                    "agent_name":   agent_name,
                    "agent_source": "paperclip",
                    "total_runs":   total,
                    "successes":    succ,
                    "failures":     fail,
                    "health_pct":   health,
                    "last_run_at":  agent_runs[-1].get("finishedAt") if agent_runs else None,
                    "dots":         dots,
                })
    except Exception as e:
        logger.warning("api_agent_health Paperclip: %s", e)

    # ── 3. Deduplicate: merge multiple Supabase rows for the same agent ──────
    # agent_health_summary may return one row per task_ref group; combine them.
    merged_supa: dict[str, dict] = {}
    for row in supa_result:
        name = row["agent_name"]
        if name not in merged_supa:
            merged_supa[name] = dict(row)
            merged_supa[name].setdefault("dots", [])
        else:
            # Accumulate totals and merge dot lists
            merged_supa[name]["total_runs"] = (merged_supa[name].get("total_runs") or 0) + (row.get("total_runs") or 0)
            merged_supa[name]["successes"]  = (merged_supa[name].get("successes")  or 0) + (row.get("successes")  or 0)
            merged_supa[name]["failures"]   = (merged_supa[name].get("failures")   or 0) + (row.get("failures")   or 0)
            merged_supa[name]["dots"] = sorted(
                merged_supa[name]["dots"] + row.get("dots", []),
                key=lambda d: d.get("created_at") or ""
            )

    # Recompute health_pct after merge
    for name, row in merged_supa.items():
        t = row.get("total_runs") or 0
        s = row.get("successes") or 0
        row["health_pct"] = round(s / t * 100, 1) if t else None

    supa_deduped = list(merged_supa.values())
    supa_names   = {r["agent_name"] for r in supa_deduped}

    # ── 4. Merge and compute system health ────────────────────────────────────
    # Use the freshest data source per agent (Supabase vs Paperclip)
    pc_by_name: dict[str, dict] = {r["agent_name"]: r for r in pc_result}
    result: list[dict] = []
    for r in supa_deduped:
        name = r["agent_name"]
        pc = pc_by_name.get(name)
        if pc:
            supa_last = r.get("last_run_at") or ""
            pc_last = pc.get("last_run_at") or ""
            if pc_last > supa_last:
                result.append(pc)
                continue
        result.append(r)
    # Add Paperclip-only agents
    for r in pc_result:
        if r["agent_name"] not in supa_names:
            result.append(r)
    valid = [r for r in result if r.get("health_pct") is not None]
    system_health = (
        round(sum(float(r["health_pct"]) for r in valid) / len(valid), 1)
        if valid else None
    )
    return JSONResponse({"agents": result, "system_health": system_health})


@app.get("/api/agent/history/{agent_name}")
async def api_agent_history(agent_name: str):
    """Last 100 run entries for one agent.
    Primary: Supabase agent_run_history (war_room dispatched agents).
    Fallback: Paperclip heartbeat-runs (all other agents).
    """
    # 1. Try Supabase first
    try:
        rows = await _supa(
            "get",
            f"agent_run_history?agent_name=eq.{agent_name}"
            "&order=created_at.desc&limit=100"
            "&select=id,status,reason,task_ref,created_at",
        )
        if rows:
            return JSONResponse(rows)
    except Exception as e:
        logger.warning("api_agent_history Supabase: %s", e)

    # 2. Fallback: Paperclip heartbeat-runs
    try:
        company_id = await _get_company_id()
        if not company_id:
            return JSONResponse([])
        async with httpx.AsyncClient(base_url=PAPERCLIP_BASE, timeout=8.0) as c:
            # Build name→id map
            ar = await c.get(f"/api/companies/{company_id}/agents")
            ar.raise_for_status()
            agents_list = ar.json() if isinstance(ar.json(), list) else []
            agent = next((a for a in agents_list if a.get("name", "").lower() == agent_name.lower()), None)
            if not agent:
                return JSONResponse([])
            agent_id = agent["id"]
            hr = await c.get(
                f"/api/companies/{company_id}/heartbeat-runs",
                params={"agentId": agent_id, "limit": 100},
            )
            hr.raise_for_status()
            runs = hr.json() if isinstance(hr.json(), list) else []
        # Convert to the same schema as agent_run_history
        converted = []
        for r in sorted(runs, key=lambda x: x.get("startedAt") or "", reverse=True):
            pc_status = r.get("status", "")
            converted.append({
                "id":         r.get("id"),
                "status":     "success" if pc_status == "succeeded" else "failed" if pc_status == "failed" else pc_status,
                "reason":     r.get("error") or None,
                "task_ref":   r.get("wakeupRequestId") or None,
                "created_at": r.get("startedAt") or r.get("createdAt"),
                "source":     r.get("invocationSource", "timer"),
            })
        return JSONResponse(converted)
    except Exception as e:
        logger.error("api_agent_history Paperclip: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        if STATE_FILE.exists():
            await websocket.send_text(json.dumps({
                "type": "initial_state",
                "state": json.loads(STATE_FILE.read_text()),
            }))
        # Keep connection alive
        while True:
            # Echo ping back as pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os as _os
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(_os.environ.get("PORT", 8765))
    print(f"🏛  War Room Dashboard (WebSocket) → http://127.0.0.1:{port}")
    print(f"    WebSocket → ws://127.0.0.1:{port}/ws")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
