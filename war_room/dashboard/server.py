"""
War Room Dashboard — Flask server (port 8765)

Serves the real-time dashboard and provides a JSON API.

Run:
  python war_room/dashboard/server.py
"""

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Flask — installed with: pip install flask
try:
    from flask import Flask, jsonify, render_template_string, request
    from flask_cors import CORS
except ImportError:
    print("Flask not installed. Run: pip install flask flask-cors")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR = Path(__file__).parent.parent
STATE_FILE   = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR     = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR   = WAR_ROOM_DIR / "agents"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")
app = Flask(__name__)
try:
    CORS(app)
except Exception:
    pass  # flask-cors optional


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>War Room Dashboard</h1><p>index.html not found.</p>"


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
    """Return mock Paperclip board state."""
    # Read from state file's paperclip section
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    return jsonify(state.get("paperclip", {
        "connected": False,
        "agents": {
            "Dexter":  {"role": "Research",    "status": "idle"},
            "David":   {"role": "Architect",   "status": "idle"},
            "Memo":    {"role": "Strategist",  "status": "idle"},
            "Hermes":  {"role": "Writer",      "status": "idle"},
        }
    }))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"🏛  War Room Dashboard → http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
