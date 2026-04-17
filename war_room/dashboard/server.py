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
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="War Room Dashboard")

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


@app.get("/api/reports")
async def api_reports():
    files = sorted(RESEARCH_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    reports = []
    for f in files[:20]:
        reports.append({
            "name":     f.name,
            "size":     f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "preview":  f.read_text(encoding="utf-8")[:300],
        })
    return JSONResponse(reports)


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
    _meeting.append(entry)
    if len(_meeting) > 300:
        _meeting.pop(0)
    await manager.broadcast({"type": "meeting_message", **entry})
    return JSONResponse(entry)


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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"🏛  War Room Dashboard (WebSocket) → http://127.0.0.1:{port}")
    print(f"    WebSocket → ws://127.0.0.1:{port}/ws")

    # Start the file watcher background task
    @app.on_event("startup")
    async def startup():
        asyncio.create_task(file_watcher())

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
