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
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Optional

import httpx

# FastAPI — installed with: pip install fastapi uvicorn websockets
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("FastAPI not installed. Run: pip install fastapi uvicorn")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR = Path(__file__).parent.parent
STATE_FILE   = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR     = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR   = WAR_ROOM_DIR / "agents"
CONFIG_FILE  = WAR_ROOM_DIR / "config.json"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")

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
app = FastAPI(title="SemeClaw War Room Agent", version="0.2.0")

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

# Write endpoints protected by bearer when SEMECLAW_API_KEY is set
_PROTECTED_WRITE_PATHS = (
    "/api/meeting/pin", "/api/meeting/unpin",
    "/api/meeting/finalize", "/api/meeting/replan",
    "/api/meeting/redirect",
    "/api/reports/delete",
)


@app.middleware("http")
async def _semeclaw_auth_and_csp(request, call_next):
    # Bearer auth on protected write endpoints
    if SEMECLAW_API_KEY and request.method != "OPTIONS":
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
    response.headers["X-SemeClaw-Version"] = "0.2.0"
    return response

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """Send a message to all connected clients."""
        data = json.dumps(message)
        dead = set()
        for ws in self.active_connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

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

            # Check for new log entries
            log_files = sorted(LOGS_DIR.glob("run-*.jsonl"), reverse=True)
            total_entries = sum(len(f.read_text().splitlines()) for f in log_files if f.exists())
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

@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
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


async def _dispatch_webhook(event: str, payload: dict) -> None:
    """Fire-and-forget POST to every registered webhook subscribed to `event`."""
    hooks = _load_webhooks()
    matches = [h for h in hooks if event in (h.get("events") or []) or "*" in (h.get("events") or [])]
    if not matches:
        return
    body = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent_version": "0.2.0",
        "tenant_id": payload.get("tenant_id", SEMECLAW_TENANT_ID),
        "data": payload,
    }
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
                        "user-agent": "SemeClaw/0.2.0",
                    },
                )
        except Exception as e:
            logger.warning("webhook %s → %s failed: %s", event, h.get("url"), e)


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


@app.get("/metrics")
async def metrics():
    """Prometheus exposition format. Scrape with Prometheus, Grafana Agent, etc."""
    from fastapi.responses import Response as FR
    lines = [
        "# HELP semeclaw_info SemeClaw agent info",
        "# TYPE semeclaw_info gauge",
        f'semeclaw_info{{version="0.2.0",tenant="{SEMECLAW_TENANT_ID}"}} 1',
    ]
    for k, v in _METRICS.items():
        lines.append(f"# HELP semeclaw_{k} Count of {k}")
        lines.append(f"# TYPE semeclaw_{k} counter")
        lines.append(f"semeclaw_{k}_total {v}")
    return FR("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


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


@app.get("/api/meeting/script")
async def api_meeting_script(name: str, lang: str = "en"):
    """Convert a report into a playable meeting script. Translates when lang != en."""
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


@app.on_event("startup")
async def _schedule_meeting_prune() -> None:
    async def _loop():
        while True:
            try:
                removed = _prune_old_meetings()
                if removed:
                    logger.info(f"meeting prune: removed {removed} files older than {MEETING_RETENTION_HOURS}h")
            except Exception as e:
                logger.warning(f"meeting prune failed: {e}")
            await asyncio.sleep(3600)  # hourly
    asyncio.create_task(_loop())


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

@app.get("/api/agent/manifest")
async def api_agent_manifest():
    """Describe what this SemeClaw agent can do. Used by consumers (NERVIX,
    Paperclip companies, direct integrators) to discover capabilities."""
    auth_required = bool(SEMECLAW_API_KEY)
    return JSONResponse({
        "id":          "semeclaw-war-room",
        "name":        "SemeClaw War Room",
        "version":     "0.2.0",
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
            "reports.list",
            "reports.content",
            "tts.synthesize",      # ElevenLabs Flash v2.5 → edge-tts fallback
            "embed.iframe",        # renders inside an iframe
            "embed.widget",        # script-tag SDK
        ],
        "endpoints": {
            "health":      "/api/agent/health",
            "manifest":    "/api/agent/manifest",
            "reports":     "/api/reports",
            "report":      "/api/reports/content?name={name}",
            "script":      "/api/meeting/script?name={name}&lang=en",
            "audio":       "/api/meeting/audio?name={name}",
            "redirect":    "/api/meeting/redirect",
            "replan":      "/api/meeting/replan",
            "finalize":    "/api/meeting/finalize",
            "pin":         "/api/meeting/pin?name={name}",
            "unpin":       "/api/meeting/unpin?file={file}&name={name}",
            "list":        "/api/meeting/list",
            "tts":         "/api/tts?text={text}&speaker={speaker}&lang=en",
            "embed_html":  "/embed?meeting={name}&v=2",
            "embed_js":    "/embed.js",
        },
        "auth": {
            "required_for_writes": auth_required,
            "scheme":   "bearer" if auth_required else "none",
            "header":   "Authorization: Bearer <SEMECLAW_API_KEY>" if auth_required else None,
            "protected_paths": list(_PROTECTED_WRITE_PATHS) if auth_required else [],
        },
        "tts": {
            "engine":          "elevenlabs-flash-v2.5 + edge-tts fallback",
            "languages":       ["en"],
            "voice_map_size":  len(_ELEVEN_VOICES),
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


@app.get("/embed.js")
async def api_embed_js():
    """Tiny JS SDK — drop-in <script> that mounts the War Room in any page.

    Usage:
        <script src="https://semeclaw.example.com/embed.js"></script>
        <div data-semeclaw-meeting="ops-review.md"
             data-semeclaw-v="2"
             style="width:100%;height:640px"></div>
    """
    from fastapi.responses import Response as FResponse
    base = SEMECLAW_PUBLIC_URL
    js = f"""(function() {{
  var BASE = {json.dumps(base)};
  function mount(el) {{
    if (el.getAttribute("data-semeclaw-mounted") === "1") return;
    el.setAttribute("data-semeclaw-mounted", "1");
    var meeting = el.getAttribute("data-semeclaw-meeting") || "";
    var layout  = el.getAttribute("data-semeclaw-v") || "1";
    var theme   = el.getAttribute("data-semeclaw-theme") || "dark";
    var url = BASE + "/embed?v=" + encodeURIComponent(layout) +
              "&theme=" + encodeURIComponent(theme) +
              (meeting ? "&meeting=" + encodeURIComponent(meeting) : "");
    var iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.width = el.style.width || "100%";
    iframe.style.height = el.style.height || "640px";
    iframe.style.border = "0";
    iframe.style.borderRadius = el.style.borderRadius || "12px";
    iframe.setAttribute("allow", "autoplay; clipboard-write");
    iframe.setAttribute("loading", "lazy");
    iframe.title = "SemeClaw War Room";
    el.innerHTML = "";
    el.appendChild(iframe);
  }}
  function scan() {{
    var nodes = document.querySelectorAll("[data-semeclaw-meeting], [data-semeclaw-embed]");
    for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", scan);
  }} else {{
    scan();
  }}
  window.SemeClaw = {{ mount: mount, scan: scan, base: BASE }};
}})();
"""
    return FResponse(
        content=js,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/embed/manifest.json")
async def api_embed_manifest():
    return JSONResponse({
        "widget": "semeclaw-war-room",
        "script_url": f"{SEMECLAW_PUBLIC_URL}/embed.js",
        "iframe_url": f"{SEMECLAW_PUBLIC_URL}/embed",
        "min_width":  320,
        "min_height": 420,
        "attributes": [
            {"name": "data-semeclaw-meeting", "required": False, "desc": "Report filename to play"},
            {"name": "data-semeclaw-v",       "required": False, "desc": "Layout version: 1 | 2 (orbital)"},
            {"name": "data-semeclaw-theme",   "required": False, "desc": "dark | light (dark only for now)"},
        ],
    })


@app.get("/embed")
async def embed_page(meeting: str = "", v: str = "1", theme: str = "dark"):
    """Serve the dashboard HTML with query-param hints for embed consumers.
    The main index.html reads window.location.search to auto-open a meeting."""
    from fastapi.responses import FileResponse
    from pathlib import Path as _P
    index = _P(__file__).parent / "index.html"
    if not index.exists():
        return JSONResponse({"error": "index not found"}, status_code=500)
    return FileResponse(index, media_type="text/html",
                        headers={"X-SemeClaw-Embed": "1",
                                 "X-SemeClaw-Meeting": meeting or "",
                                 "X-SemeClaw-Layout": v})


@app.get("/api/agents")
async def api_agents():
    agents = {}
    for md_file in AGENTS_DIR.glob("*.md"):
        agent_id = md_file.stem
        text = md_file.read_text(encoding="utf-8")
        name = agent_id.capitalize()
        for line in text.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
                break
        agents[agent_id] = {"id": agent_id, "name": name}
    return JSONResponse(agents)


@app.post("/api/run")
async def api_run(request: Request):
    data = await request.json()
    task = data.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "task required"}, status_code=400)

    agents = data.get("agents", "research,strategist,writer")
    project = data.get("project", "NERVIX")

    # Broadcast that a task is starting
    await manager.broadcast({
        "type": "task_started",
        "task": task,
        "agents": agents.split(","),
        "project": project,
    })

    # Run war_room.py as a subprocess so it doesn't block
    war_room_script = WAR_ROOM_DIR / "war_room.py"
    cmd = [
        sys.executable,
        str(war_room_script),
        "run",
        task,
        f"--agents={agents}",
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

# Free model waterfall: OpenRouter free → local Ollama
_AI_MODELS = [
    ("openrouter", "qwen/qwen3.6-plus:free"),
    ("openrouter", "google/gemma-3-27b-it:free"),
    ("openrouter", "qwen/qwen3.5-72b:free"),
    ("ollama",     "qwen3:8b"),
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
You are the War Room Orchestrator AI. Generate a meeting script where AI agents collaboratively plan a task.

AGENTS:
- Orchestrator: Strategic coordination, task decomposition, final assignments
- Dexter: Senior developer, architecture, backend systems, DevOps, NERVIX platform
- Memo: Project manager, planning, workflows, n8n automations, MyWork framework
- Sienna: Crypto/finance specialist, TON/Solana blockchain, smarty.me payments
- Nano: Agent creator, NERVIX agents, frontend builds, CLI tooling
- GSD: Execution planner, phase breakdown, sprint planning, deliverables
- Hermes: Telegram bots, communications routing, messaging infrastructure
- Pi: AI researcher, model selection, pipelines, LLM integrations

RULES:
1. Orchestrator opens with a 2-3 sentence analysis of the task
2. Invite 2-4 agents most relevant to this specific task (not all of them)
3. Each invited agent speaks 1-2 sentences about their concrete piece of the work
4. Include ONE clarifying question from an agent ONLY if genuinely needed — skip if the task is clear
5. Orchestrator closes with concrete, specific assignments per agent

OUTPUT FORMAT: strict JSON only, no markdown fences, no extra text:
{
  "title": "short meeting title (5-8 words)",
  "narrator_intro": "2-3 sentences. State the task briefly, then explain WHY each selected agent is ideal for this task — one specific reason per agent.",
  "agents": ["Orchestrator", "Dexter"],
  "turns": [
    {"speaker": "Orchestrator", "text": "...", "type": "intro"},
    {"speaker": "Dexter", "text": "...", "type": "expertise"},
    {"speaker": "Memo", "text": "...", "type": "question", "to_user": true, "question": "One specific question?"},
    {"speaker": "Dexter", "text": "...", "type": "assignment", "tasks": ["Build X", "Deploy Y"]},
    {"speaker": "Memo", "text": "...", "type": "assignment", "tasks": ["Track Z"]},
    {"speaker": "Orchestrator", "text": "...", "type": "close"}
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
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OPENROUTER_BASE, timeout=45.0) as c:
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
        "options": {"num_predict": 1200},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=90.0) as c:
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


@app.get("/api/tts")
async def api_tts(text: str, speaker: str = "", lang: str = "en"):
    """Stream MP3 audio for a given text + speaker using edge-tts neural voices.

    Returns: audio/mpeg binary — play directly with <audio> or AudioContext.
    Cached in memory (LRU-style) to avoid repeated network calls for the same phrase.
    """
    import io
    try:
        import edge_tts
    except ImportError:
        return JSONResponse({"error": "edge_tts not installed"}, status_code=503)

    from fastapi.responses import Response as FResponse

    # ── ElevenLabs Flash v2.5 path (English only, key required) ────────────
    if _ELEVEN_KEY and (not lang or lang == "en") and speaker in _ELEVEN_VOICES:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                voice_id = await _resolve_eleven_voice_id(client, _ELEVEN_VOICES[speaker])
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
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True},
                        },
                    )
                    if resp.status_code == 200 and resp.content:
                        return FResponse(
                            content=resp.content,
                            media_type="audio/mpeg",
                            headers={
                                "Cache-Control": "public, max-age=3600",
                                "X-Speaker": speaker,
                                "X-Voice": _ELEVEN_VOICES[speaker],
                                "X-TTS-Engine": "elevenlabs-flash-v2.5",
                            },
                        )
                    else:
                        logger.warning(f"ElevenLabs {resp.status_code} for {speaker}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"ElevenLabs fallback to edge-tts for {speaker}: {e}")

    cfg = _AGENT_VOICES.get(speaker, _DEFAULT_TTS)
    # Language override: swap to gender-matched native voice when non-English
    if lang and lang != "en":
        lang_pool = _LANG_VOICE_MAP.get(lang)
        if lang_pool:
            gender = _AGENT_GENDER.get(speaker, "m")
            pool = lang_pool.get(gender, lang_pool.get("m", []))
            if pool:
                cfg = {**cfg, "voice": pool[hash(speaker) % len(pool)]}
    voice = cfg["voice"]
    rate  = cfg["rate"]
    pitch = cfg["pitch"]

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        audio_bytes = buf.read()
        if not audio_bytes:
            return JSONResponse({"error": "no audio generated"}, status_code=500)
        return FResponse(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Speaker": speaker,
                "X-Voice": voice,
            }
        )
    except Exception as e:
        logger.error(f"TTS error for speaker={speaker}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/board")
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
                if agent_name in supa_names:
                    continue   # already covered by Supabase
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

    # ── 3. Merge and compute system health ────────────────────────────────────
    result = supa_result + pc_result
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

    # Start the file watcher background task
    @app.on_event("startup")
    async def startup():
        asyncio.create_task(file_watcher())

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
