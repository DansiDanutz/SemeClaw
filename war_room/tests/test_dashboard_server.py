"""Tests for the War Room dashboard server endpoints."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Bootstrap path so dashboard modules import
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

os.chdir(str(ROOT))

from war_room.dashboard.server import app, RESEARCH_DIR, STATE_FILE


@pytest.fixture
def client(tmp_path):
    # Isolate research dir and state file for tests
    with patch("war_room.dashboard.server.RESEARCH_DIR", tmp_path / "research"):
        with patch("war_room.dashboard.server.STATE_FILE", tmp_path / "state.json"):
            (tmp_path / "research").mkdir(exist_ok=True)
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c


def test_tts_health_returns_neural_when_edge_ready(client):
    r = client.get("/api/tts/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ready"] is True
    assert data["engine"] in ("elevenlabs", "neural")


def test_tts_missing_text(client):
    r = client.get("/api/tts?text=")
    assert r.status_code == 400


def test_agent_bio_missing_params(client):
    r = client.get("/api/agents/bio")
    assert r.status_code == 400


def test_meeting_finalize_missing_name(client):
    r = client.post("/api/meeting/finalize", json={})
    assert r.status_code == 400
    assert "name required" in r.get_json()["error"]


def test_meeting_finalize_report_not_found(client):
    r = client.post("/api/meeting/finalize", json={"name": "missing.md"})
    assert r.status_code == 404


def test_meeting_finalize_appends_qa_and_verdict(client, tmp_path):
    report = tmp_path / "research" / "test-report.md"
    report.write_text("# Test Report\n\nBody here.\n", encoding="utf-8")

    with patch("war_room.dashboard.server._run_verdict", return_value="Looks good.\n\nVERDICT: CORRECT — proceed"):
        r = client.post("/api/meeting/finalize", json={
            "name": "test-report.md",
            "qa_pairs": [
                {"question": "How long?", "responder": "GSD", "response": "6 weeks."}
            ],
        })

    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "finalized"
    assert "CORRECT" in data["verdict"]

    content = report.read_text(encoding="utf-8")
    assert "## 🎤 Meeting Q&A" in content
    assert "6 weeks." in content
    assert "## 🔎 Updated Analysis" in content


def test_meeting_pin(client, tmp_path):
    r = client.post("/api/meeting/pin", json={"name": "launch-review.md"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "pinned"
    assert data["name"] == "launch-review.md"

    # State file should contain pinned list
    state = json.loads((tmp_path / "state.json").read_text())
    assert "launch-review.md" in state["pinned_reports"]


def test_cleanup_removes_old_files(client, tmp_path):
    research = tmp_path / "research"
    research.mkdir(exist_ok=True)

    old = research / "old.md"
    old.write_text("old")
    # Manually set mtime to 3 days ago
    os.utime(old, (0, 0))

    r = client.post("/api/cleanup")
    assert r.status_code == 200
    data = r.get_json()
    assert data["reports_removed"] >= 1
    assert old.exists() is False
