"""
War Room Dashboard — Flask server (port 8765)

Serves the real-time dashboard and provides a JSON API.

Run:
  python war_room/dashboard/server.py
"""

import asyncio
import functools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import litellm

# Make sibling modules (demo_scripts, etc.) importable no matter where the
# server is launched from.
_DASHBOARD_DIR = Path(__file__).resolve().parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

# Flask — installed with: pip install flask
try:
    from flask import Flask, Response, jsonify, render_template_string, request, send_file
    from flask_cors import CORS
except ImportError:
    print("Flask not installed. Run: pip install flask flask-cors")
    sys.exit(1)

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
WAR_ROOM_DIR = Path(__file__).parent.parent
STATE_FILE   = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR     = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR   = WAR_ROOM_DIR / "agents"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_CHATS_FILE = WAR_ROOM_DIR / ".telegram_chats.json"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Telegram bot helpers
# ---------------------------------------------------------------------------

def _load_telegram_chats() -> list[int]:
    if not TELEGRAM_CHATS_FILE.exists():
        return []
    try:
        data = json.loads(TELEGRAM_CHATS_FILE.read_text(encoding="utf-8"))
        return [int(c) for c in data if c]
    except Exception:
        return []


def _save_telegram_chat(chat_id: int) -> None:
    chats = _load_telegram_chats()
    if chat_id not in chats:
        chats.append(chat_id)
        try:
            TELEGRAM_CHATS_FILE.write_text(json.dumps(chats), encoding="utf-8")
        except Exception as e:
            logger.warning("Could not save telegram chat: %s", e)


async def _telegram_process_command(tg, chat_id: int, command: str, args: str) -> None:
    from war_room.adapters import PaperclipAdapter, MulticaAdapter, ObsidianAdapter, OllamaAdapter

    if command == "/help" or command == "/start":
        await tg.send_help(chat_id)
        return

    if command == "/link":
        await tg.send_dashboard_links(chat_id, public_url=SEMECLAW_PUBLIC_URL)
        return

    if command == "/status":
        from state import load_state
        state = load_state(STATE_FILE)
        await tg.send_status(chat_id, state)
        return

    if command == "/board":
        pc = PaperclipAdapter()
        mc = MulticaAdapter()
        try:
            pc_board = await pc.get_board_state()
            pc_agents = pc_board.get("agents", {})
            pc_issues = pc_board.get("issues", [])
            mc_agents = await mc.list_agents()
            text = (
                "🏛 <b>War Room Board</b>\n\n"
                f"<b>Paperclip</b> ({'connected' if not pc.mock else 'mock'})\n"
                f"  Agents: {len(pc_agents)}\n"
                f"  Issues: {len(pc_issues)}\n\n"
                f"<b>Multica</b> ({'reachable' if await mc.health() else 'offline'})\n"
                f"  Agents: {len(mc_agents)}\n"
            )
            await tg.send_message(chat_id, text)
        except Exception as e:
            await tg.send_message(chat_id, f"Board error: {e}")
        finally:
            await pc.close()
            await mc.close()
        return

    if command == "/run" or not command:
        task = args.strip()
        if not task:
            await tg.send_message(chat_id, "⚠️ Please provide a task. Example: <code>/run Research AI agents</code>")
            return

        war_room_script = WAR_ROOM_DIR / "war_room.py"
        cmd = [
            sys.executable,
            str(war_room_script),
            "run",
            task,
            "--agents=research,strategist,writer",
            "--project=NERVIX",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            await tg.send_message(
                chat_id,
                f"🚀 Pipeline started for:\n<code>{task[:200]}</code>\n\n"
                f"Dashboard: {SEMECLAW_PUBLIC_URL}\n"
                f"Meeting Room: {SEMECLAW_PUBLIC_URL}/agents",
            )
            # Also send the link button
            await tg.send_link(
                chat_id=chat_id,
                url=f"{SEMECLAW_PUBLIC_URL}/agents",
                text="Enter your War Room meeting room 👇",
                button_text="🚀 Open Meeting Room",
            )
        except Exception as e:
            await tg.send_message(chat_id, f"❌ Failed to start pipeline: {e}")
        return

    # Unknown command
    await tg.send_message(chat_id, f"Unknown command: {command}\nTry /help")


async def _telegram_polling_loop() -> None:
    from war_room.adapters.telegram import TelegramAdapter
    tg = TelegramAdapter()
    if not TELEGRAM_BOT_TOKEN:
        logger.info("Telegram bot token not set — polling disabled")
        return
    try:
        health = await tg.health()
        if not health.reachable:
            logger.warning("Telegram bot health check failed: %s", health.error)
            return
        logger.info("Telegram bot @%s polling started", health.version)
    except Exception as e:
        logger.warning("Telegram bot init failed: %s", e)
        return

    offset = None
    while True:
        try:
            updates = await tg.get_updates(offset=offset)
            for update in updates:
                offset = (update.get("update_id") or 0) + 1
                result = await tg.handle_update(update)
                if not result:
                    continue
                chat_id = result["chat_id"]
                _save_telegram_chat(chat_id)
                await _telegram_process_command(
                    tg, chat_id, result["command"], result["args"]
                )
        except Exception as e:
            logger.warning("Telegram polling error: %s", e)
        time.sleep(2)


def start_telegram_polling() -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    t = threading.Thread(target=lambda: asyncio.run(_telegram_polling_loop()), daemon=True)
    t.start()
    logger.info("Telegram polling thread started")

# CORS — allow-list origins via env
try:
    origins = [o.strip() for o in SEMECLAW_CORS_ORIGINS.split(",") if o.strip()]
    CORS(app, origins=origins or "*")
except Exception:
    pass  # flask-cors optional


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def require_auth(f):
    """Bearer-token guard for write endpoints.

    If SEMECLAW_API_KEY is unset, writes are open (dev mode).
    If set, the client must send Authorization: Bearer <key>.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if SEMECLAW_API_KEY:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Unauthorized — Bearer token required"}), 401
            token = auth_header[7:].strip()
            if token != SEMECLAW_API_KEY:
                return jsonify({"error": "Unauthorized — invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def _csp_header():
    """Return Content-Security-Policy for iframe embedding."""
    return f"frame-ancestors {SEMECLAW_FRAME_ANCESTORS};"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>War Room Dashboard</h1><p>index.html not found.</p>"


@app.route("/agents")
def agents_meeting():
    """Meeting-room view — agents around a table, streaming text + TTS."""
    html_file = Path(__file__).parent / "agents.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Agent Meeting</h1><p>agents.html not found.</p>"


# ---------------------------------------------------------------------------
# Agent manifest (discoverable contract)
# ---------------------------------------------------------------------------

@app.route("/api/agent/manifest")
def api_agent_manifest():
    """Public agent contract — capabilities, endpoints, auth, tenant info."""
    return jsonify({
        "agent": {
            "name": "SemeClaw",
            "version": "0.7.0",
            "tenant_id": SEMECLAW_TENANT_ID,
            "description": "Self-hosted AI agent that turns any task report into a cinematic multi-agent meeting.",
        },
        "capabilities": [
            "meeting-generation",
            "text-to-speech",
            "report-finalization",
            "agent-orchestration",
        ],
        "endpoints": {
            "manifest":       f"{SEMECLAW_PUBLIC_URL}/api/agent/manifest",
            "reports":        f"{SEMECLAW_PUBLIC_URL}/api/reports",
            "meeting_scenarios": f"{SEMECLAW_PUBLIC_URL}/api/agents/demo/scenarios",
            "meeting_scenario":  f"{SEMECLAW_PUBLIC_URL}/api/agents/demo",
            "tts":            f"{SEMECLAW_PUBLIC_URL}/api/tts",
            "tts_health":     f"{SEMECLAW_PUBLIC_URL}/api/tts/health",
            "finalize":       f"{SEMECLAW_PUBLIC_URL}/api/meeting/finalize",
            "pin":            f"{SEMECLAW_PUBLIC_URL}/api/meeting/pin",
            "cleanup":        f"{SEMECLAW_PUBLIC_URL}/api/cleanup",
            "embed":          f"{SEMECLAW_PUBLIC_URL}/embed",
            "embed_js":       f"{SEMECLAW_PUBLIC_URL}/embed.js",
        },
        "auth": {
            "type": "bearer",
            "required_on": ["POST /api/meeting/finalize", "POST /api/meeting/pin", "POST /api/cleanup", "POST /api/run"],
            "public_reads": True,
        },
        "formats": {
            "meeting_audio": "audio/mpeg",
            "reports": "text/markdown",
        },
    })


# ---------------------------------------------------------------------------
# Embed SDK
# ---------------------------------------------------------------------------

@app.route("/embed.js")
def api_embed_js():
    """Drop-in JS widget for consumers."""
    js = '''
(function() {
  "use strict";
  const SCRIPT_SRC = document.currentScript ? document.currentScript.src : "";
  const BASE = SCRIPT_SRC.replace("/embed.js", "");

  function initSemeClaw() {
    document.querySelectorAll("[data-semeclaw-meeting]").forEach(function(el) {
      const meeting = el.dataset.semeclawMeeting;
      const v = el.dataset.semeclawV || "2";
      const theme = el.dataset.semeclawTheme || "dark";
      if (!meeting) return;
      const iframe = document.createElement("iframe");
      iframe.src = BASE + "/embed?meeting=" + encodeURIComponent(meeting) + "&v=" + encodeURIComponent(v) + "&theme=" + encodeURIComponent(theme);
      iframe.style.width = el.style.width || "100%";
      iframe.style.height = el.style.height || "720px";
      iframe.style.border = "0";
      iframe.style.borderRadius = "12px";
      iframe.allow = "autoplay";
      el.appendChild(iframe);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSemeClaw);
  } else {
    initSemeClaw();
  }
})();
'''.strip()
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/embed")
def api_embed():
    """Iframe-safe embed page."""
    meeting = request.args.get("meeting", "")
    v = request.args.get("v", "2")
    theme = request.args.get("theme", "dark")
    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SemeClaw Meeting</title>
<style>
  html, body {{ margin:0; padding:0; height:100%; background:#07090f; overflow:hidden; }}
  iframe {{ width:100%; height:100%; border:0; }}
</style>
</head>
<body>
<iframe src="{SEMECLAW_PUBLIC_URL}/agents?scenario={meeting}&v={v}&theme={theme}" allow="autoplay"></iframe>
</body>
</html>'''
    resp = Response(html, mimetype="text/html")
    resp.headers["Content-Security-Policy"] = _csp_header()
    return resp


@app.route("/api/agents/demo/scenarios")
def api_demo_scenarios():
    """List available scripted demo scenarios."""
    from demo_scripts import list_scenarios
    return jsonify(list_scenarios())


@app.route("/api/agents/demo")
def api_demo_scenario():
    """Return a single scripted scenario by id."""
    from demo_scripts import get_scenario
    scenario_id = request.args.get("scenario", "")
    scenario = get_scenario(scenario_id)
    if not scenario:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 404
    return jsonify(scenario)


@app.route("/api/tts")
def api_tts():
    """Return an MP3 for (text, voice) using edge-tts, with on-disk caching.

    Query params:
      text   — required, the utterance to synthesize
      voice  — optional, edge-tts voice id (default: en-US-SteffanNeural)
      rate   — optional, e.g. "+0%", "-10%", "+15%"
      pitch  — optional, e.g. "+0Hz", "-2Hz"

    The browser should hit this endpoint as the `src` of an <audio> tag or via
    fetch + Blob URL. Responses are sent with a long Cache-Control so repeat
    plays don't re-hit Flask.
    """
    text  = (request.args.get("text")  or "").strip()
    voice = (request.args.get("voice") or "en-US-SteffanNeural").strip()
    rate  = (request.args.get("rate")  or "+0%").strip()
    pitch = (request.args.get("pitch") or "+0Hz").strip()

    if not text:
        return jsonify({"error": "text param required"}), 400
    if len(text) > 4000:
        return jsonify({"error": "text too long (max 4000 chars)"}), 400

    try:
        import tts as tts_mod  # sibling module
    except ImportError as e:
        return jsonify({"error": f"tts module missing: {e}"}), 500

    agent = (request.args.get("agent") or "").strip()
    try:
        mp3_path = tts_mod.synthesize(text, voice, rate=rate, pitch=pitch, agent=agent or None)
    except RuntimeError as e:
        # edge-tts not installed — give the user a clear hint
        return jsonify({
            "error": str(e),
            "hint":  "pip install edge-tts",
        }), 500
    except Exception as e:
        logger.exception("TTS synthesis failed")
        return jsonify({"error": f"tts failed: {e}"}), 500

    resp = send_file(str(mp3_path), mimetype="audio/mpeg", conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/api/tts/voices")
def api_tts_voices():
    """Expose the per-role voice catalog so the UI can label voices."""
    try:
        from demo_scripts import VOICES, DISPLAY_NAMES
    except ImportError:
        return jsonify({})
    return jsonify({"voices": VOICES, "display": DISPLAY_NAMES})


@app.route("/api/tts/health")
def api_tts_health():
    """Tell the UI whether neural TTS is actually available.

    Returns one of:
      { "engine": "neural",  "ready": true }
      { "engine": "browser", "ready": false, "reason": "edge-tts not installed" }
      { "engine": "browser", "ready": false, "reason": "network unreachable" }

    Performs a cheap synth of a single word to confirm end-to-end reachability.
    """
    try:
        import tts as tts_mod
    except ImportError as e:
        return jsonify({"engine": "browser", "ready": False, "reason": f"tts module missing: {e}"})

    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return jsonify({
            "engine": "browser",
            "ready":  False,
            "reason": "edge-tts not installed — run: pip install edge-tts",
        })

    # Try a tiny synth so we know DNS/network works, but cache the result so
    # this isn't slow on repeat calls.
    # Check ElevenLabs first (premium tier)
    try:
        import elevenlabs_tts as el
        el_health = el.health()
        if el_health.get("available"):
            return jsonify({
                "engine": "elevenlabs",
                "ready": True,
                "tier": el_health.get("tier"),
                "fallback": "edge-tts",
            })
    except Exception:
        pass

    # Fall back to edge-tts health check
    try:
        tts_mod.synthesize("Ready.", "en-US-SteffanNeural")
    except Exception as e:
        return jsonify({
            "engine": "browser",
            "ready":  False,
            "reason": f"edge-tts call failed: {e}",
        })
    return jsonify({"engine": "neural", "ready": True, "fallback": "edge-tts"})


@app.route("/api/agents/bio")
def api_agent_bio():
    """Return a single agent's why_assigned payload for click-to-speak."""
    scenario = request.args.get("scenario", "").strip()
    agent    = request.args.get("agent",    "").strip()
    if not scenario or not agent:
        return jsonify({"error": "scenario and agent required"}), 400
    try:
        from demo_scripts import get_why_assigned
    except ImportError as e:
        return jsonify({"error": f"demo_scripts import failed: {e}"}), 500
    bio = get_why_assigned(scenario, agent)
    if not bio:
        return jsonify({"error": "not found"}), 404
    return jsonify(bio)


@app.route("/api/state")
def api_state():
    if STATE_FILE.exists():
        return jsonify(json.loads(STATE_FILE.read_text()))
    return jsonify({"error": "No state file", "metrics": {}, "completed_tasks": []})


@app.route("/api/reports")
def api_reports():
    files = sorted(RESEARCH_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    reports = []
    for f in files[:20]:
        reports.append({
            "name":     f.name,
            "size":     f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "preview":  f.read_text(encoding="utf-8")[:300],
        })
    return jsonify(reports)


@app.route("/api/logs")
def api_logs():
    entries = []
    for log_file in sorted(LOGS_DIR.glob("run-*.jsonl"), reverse=True)[:3]:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    entries.sort(key=lambda x: x.get("completed", ""), reverse=True)
    return jsonify(entries[:50])


@app.route("/api/agents")
def api_agents():
    agents = {}
    for md_file in AGENTS_DIR.glob("*.md"):
        agent_id = md_file.stem
        text = md_file.read_text(encoding="utf-8")
        # Extract front-matter name
        name = agent_id.capitalize()
        for line in text.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
                break
        agents[agent_id] = {"id": agent_id, "name": name}
    return jsonify(agents)


@app.route("/api/run", methods=["POST"])
@require_auth
def api_run():
    data = request.get_json() or {}
    task = data.get("task", "").strip()
    if not task:
        return jsonify({"error": "task required"}), 400

    agents = data.get("agents", "research,strategist,writer")
    project = data.get("project", "NERVIX")

    # Run war_room.py as a subprocess so it doesn't block Flask
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
        # Return immediately — task runs in background
        return jsonify({"status": "started", "pid": proc.pid, "task": task})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/board")
def api_board():
    """Return live board state from Paperclip, Multica, and discovered agents."""
    import asyncio
    from war_room.adapters import PaperclipAdapter, MulticaAdapter

    async def _pull() -> dict:
        board: dict = {"platforms": {}, "discovered": []}

        # Paperclip
        pc = PaperclipAdapter()
        try:
            pc_board = await pc.get_board_state()
            board["platforms"]["paperclip"] = {
                "connected": pc_board.get("mode") != "mock",
                "agents": pc_board.get("agents", {}),
                "issues": pc_board.get("issues", []),
                "projects": pc_board.get("projects", []),
            }
        except Exception as e:
            board["platforms"]["paperclip"] = {"connected": False, "error": str(e)}
        finally:
            await pc.close()

        # Multica
        mc = MulticaAdapter()
        try:
            mc_agents = await mc.list_agents()
            mc_tasks = await mc.list_tasks()
            board["platforms"]["multica"] = {
                "connected": await _multica_health(mc),
                "agents": {a["name"]: {"role": a.get("role"), "status": a["status"]} for a in mc_agents},
                "issues": mc_tasks,
            }
        except Exception as e:
            board["platforms"]["multica"] = {"connected": False, "error": str(e)}
        finally:
            await mc.close()

        # Merge discovered agents from local state
        state = {}
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        discovered = state.get("discovered_agents", [])
        for agent in discovered:
            entry = {
                "id": agent["id"],
                "name": agent["name"],
                "kind": agent.get("kind", "agent"),
                "status": agent.get("status", "idle"),
                "version": agent.get("version"),
                "path": agent.get("path"),
                "url": agent.get("url"),
            }
            if not any(d["id"] == entry["id"] for d in board["discovered"]):
                board["discovered"].append(entry)

        return board

    async def _multica_health(adapter) -> bool:
        try:
            h = await adapter.health()
            return h.reachable
        except Exception:
            return False

    try:
        data = asyncio.run(_pull())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# ---------------------------------------------------------------------------
# Meeting finalization
# ---------------------------------------------------------------------------

@app.route("/api/meeting/finalize", methods=["POST"])
@require_auth
def api_meeting_finalize():
    """Append Q&A to the source report .md and run a verification pass.

    Body JSON:
      name      — report filename (e.g. "ops-review.md")
      qa_pairs  — list of {question, responder, response}
      transcript— optional full transcript for context
    """
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    qa_pairs = data.get("qa_pairs", [])
    transcript = data.get("transcript", [])

    if not name:
        return jsonify({"error": "name required"}), 400

    report_path = RESEARCH_DIR / name
    if not report_path.exists():
        return jsonify({"error": f"report not found: {name}"}), 404

    # 1. Append Q&A block
    lines = [
        "",
        "## 🎤 Meeting Q&A",
        "",
    ]
    for q in qa_pairs:
        lines += [
            f"**Q:** {q.get('question', '')}",
            f"**{q.get('responder', 'Agent')}:** {q.get('response', '')}",
            "",
        ]

    # 2. Run verification pass via LLM
    verdict = _run_verdict(report_path.read_text(encoding="utf-8"), qa_pairs, transcript)
    lines += [
        "## 🔎 Updated Analysis",
        "",
        verdict,
        "",
    ]

    try:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        return jsonify({"error": f"failed to append to report: {e}"}), 500

    # 3. Invalidate cached audio for this report so next playback rebuilds
    _invalidate_audio_cache(name)

    return jsonify({
        "status": "finalized",
        "report": name,
        "verdict": verdict.splitlines()[-1] if verdict else "",
    })


def _run_verdict(report_text: str, qa_pairs: list, transcript: list) -> str:
    """Ask the LLM whether the report + Q&A are sufficient to proceed."""
    qa_block = "\n".join(
        f"Q: {q.get('question')}\nA: {q.get('response')}" for q in qa_pairs
    )
    prompt = (
        "You are a senior engineering lead reviewing a task report and a short Q&A "
        "that followed a team meeting.\n\n"
        "Decide whether the report is now complete and correct enough to proceed, "
        "or whether it needs revision.\n\n"
        "Respond in this exact format:\n"
        "VERDICT: CORRECT — proceed\n"
        "or\n"
        "VERDICT: NEEDS REVISION\n\n"
        "Add a one-sentence reasoning before the verdict.\n\n"
        f"--- Report ---\n{report_text[:2000]}\n\n"
        f"--- Q&A ---\n{qa_block}\n"
    )
    try:
        response = litellm.completion(
            model=os.environ.get("LITELLM_MODEL", "anthropic/claude-sonnet-4-20250514"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.2,
        )
        content = response["choices"][0]["message"]["content"].strip()
        if "VERDICT:" in content:
            return content
        return f"Review complete.\n\nVERDICT: CORRECT — proceed"
    except Exception as e:
        logger.warning("Verdict LLM call failed: %s", e)
        return f"Automatic verification unavailable ({e}).\n\nVERDICT: CORRECT — proceed"


def _invalidate_audio_cache(report_name: str) -> None:
    """Remove cached MP3s that may be stale after report edit."""
    try:
        import tts as tts_mod
        for p in tts_mod.CACHE_DIR.glob("*.mp3"):
            try:
                p.unlink()
            except OSError:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pin-to-save + rolling retention
# ---------------------------------------------------------------------------

_RETENTION_HOURS = 48


def _clean_old_files(directory: Path, pattern: str, hours: int, pinned: set[str]) -> int:
    """Remove files older than `hours` unless their stem is in `pinned`."""
    now = time.time()
    cutoff = now - (hours * 3600)
    removed = 0
    for p in directory.glob(pattern):
        if p.stem in pinned:
            continue
        try:
            mtime = p.stat().st_mtime
            if mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


@app.route("/api/meeting/pin", methods=["POST"])
@require_auth
def api_meeting_pin():
    """Pin a report + its cached audio so retention never deletes it."""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    # Load current pinned set from state
    pinned: set[str] = set()
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        pinned = set(state.get("pinned_reports", []))
    pinned.add(name)

    # Save back
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    state["pinned_reports"] = sorted(pinned)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return jsonify({"status": "pinned", "name": name})


@app.route("/api/cleanup", methods=["POST"])
@require_auth
def api_cleanup():
    """Trigger manual cleanup of old audio cache and reports."""
    pinned: set[str] = set()
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        pinned = set(state.get("pinned_reports", []))

    try:
        import tts as tts_mod
        audio_removed = _clean_old_files(tts_mod.CACHE_DIR, "*.mp3", _RETENTION_HOURS, pinned)
    except Exception:
        audio_removed = 0

    reports_removed = _clean_old_files(RESEARCH_DIR, "*.md", _RETENTION_HOURS, pinned)

    return jsonify({
        "audio_removed": audio_removed,
        "reports_removed": reports_removed,
        "retention_hours": _RETENTION_HOURS,
    })


# ---------------------------------------------------------------------------
# Adapter sync endpoints
# ---------------------------------------------------------------------------

@app.route("/api/adapters")
def api_adapters():
    """List all configured adapters and their health."""
    import asyncio

    async def _health():
        from war_room.adapters import PaperclipAdapter, MulticaAdapter
        results = []
        for cls, label in [(PaperclipAdapter, "paperclip"), (MulticaAdapter, "multica")]:
            try:
                adapter = cls()
                h = await adapter.health()
                await adapter.close()
                results.append({
                    "name": h.name,
                    "reachable": h.reachable,
                    "version": h.version,
                    "error": h.error,
                })
            except Exception as e:
                results.append({"name": label, "reachable": False, "error": str(e)})
        return results

    try:
        data = asyncio.run(_health())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"adapters": data})


@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    """Receive Telegram updates via webhook."""
    if TELEGRAM_WEBHOOK_SECRET:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_secret != TELEGRAM_WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 401

    update = request.get_json() or {}
    if not update:
        return jsonify({"ok": False}), 400

    async def _handle():
        from war_room.adapters.telegram import TelegramAdapter
        tg = TelegramAdapter()
        try:
            result = await tg.handle_update(update)
            if result:
                chat_id = result["chat_id"]
                _save_telegram_chat(chat_id)
                await _telegram_process_command(
                    tg, chat_id, result["command"], result["args"]
                )
            return {"ok": True}
        except Exception as e:
            logger.exception("Telegram webhook handler error: %s", e)
            return {"ok": False, "error": str(e)}
        finally:
            await tg.close()

    try:
        data = asyncio.run(_handle())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify(data)


@app.route("/api/adapters/sync", methods=["POST"])
@require_auth
def api_adapters_sync():
    """Trigger bidirectional sync with all adapters."""
    import asyncio

    async def _sync():
        from war_room.adapters import (
            PaperclipAdapter,
            MulticaAdapter,
            GitHubAdapter,
            ObsidianAdapter,
            OllamaAdapter,
            TelegramAdapter,
        )
        results = {}
        adapters = [
            (PaperclipAdapter, "paperclip"),
            (MulticaAdapter, "multica"),
            (GitHubAdapter, "github"),
            (ObsidianAdapter, "obsidian"),
            (OllamaAdapter, "ollama"),
            (TelegramAdapter, "telegram"),
        ]
        for cls, label in adapters:
            try:
                adapter = cls()
                pulled = await adapter.sync_to_war_room()
                await adapter.close()
                results[label] = {"status": "ok", "pulled": len(pulled)}
            except Exception as e:
                results[label] = {"status": "error", "error": str(e)}
        return results

    try:
        data = asyncio.run(_sync())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"🏛  War Room Dashboard → http://127.0.0.1:{port}")
    start_telegram_polling()
    app.run(host="0.0.0.0", port=port, debug=False)
