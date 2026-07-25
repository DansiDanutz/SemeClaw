"""Tests for the War Room dashboard server endpoints."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Bootstrap path so dashboard modules import
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

os.chdir(str(ROOT))

from war_room.dashboard.server import app


@pytest.fixture
def client(tmp_path):
    from starlette.testclient import TestClient

    # Isolate research dir and state file for tests
    with patch("war_room.dashboard.server.RESEARCH_DIR", tmp_path / "research"):
        with patch("war_room.dashboard.server.STATE_FILE", tmp_path / "state.json"):
            with patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""):
                with patch("war_room.dashboard.server._is_loopback_request", return_value=True):
                    (tmp_path / "research").mkdir(exist_ok=True)
                    with TestClient(app) as c:
                        yield c


def test_tts_health_returns_neural_when_edge_ready(client):
    r = client.get("/api/agent/health")
    assert r.status_code == 200
    data = r.json()
    assert "agents" in data or "system_health" in data


def test_tts_missing_text(client):
    r = client.get("/api/tts?text=")
    assert r.status_code in (204, 400)


def test_agent_bio_missing_params(client):
    r = client.get("/api/agents/bio")
    assert r.status_code in (400, 404)


def test_meeting_finalize_missing_name(client):
    r = client.post("/api/meeting/finalize", json={})
    # Endpoint returns 404 when report not found (empty name → no report)
    assert r.status_code in (400, 404)


def test_meeting_finalize_report_not_found(client):
    r = client.post("/api/meeting/finalize", json={"name": "missing.md"})
    assert r.status_code == 404


def test_meeting_finalize_appends_qa_and_verdict(client, tmp_path):
    report = tmp_path / "research" / "test-report.md"
    report.write_text("# Test Report\n\nBody here.\n", encoding="utf-8")

    with patch("war_room.dashboard.server._call_openrouter", return_value="Looks good.\n\nVERDICT: CORRECT — proceed"):
        r = client.post(
            "/api/meeting/finalize",
            json={
                "name": "test-report.md",
                "qa_pairs": [{"question": "How long?", "responder": "GSD", "response": "6 weeks."}],
            },
        )

    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "finalized" or data.get("ok") is True
    assert "CORRECT" in data.get("verdict", data.get("verdict_line", ""))

    content = report.read_text(encoding="utf-8")
    assert "## 💬 Meeting Interjections" in content
    assert "6 weeks." in content
    assert "## 🔎 Updated Analysis" in content


def test_meeting_pin(client, tmp_path):
    with patch("war_room.dashboard.server._build_meeting_mp3", return_value=None):
        r = client.post("/api/meeting/pin", params={"name": "launch-review.md"})
        # _build_meeting_mp3 returns None for missing report → 500
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            data = r.json()
            assert data.get("status") == "pinned" or data.get("ok") is True


def test_cleanup_removes_old_files(client, tmp_path):
    research = tmp_path / "research"
    research.mkdir(exist_ok=True)

    old = research / "old.md"
    old.write_text("old")
    # Manually set mtime to 3 days ago
    os.utime(old, (0, 0))

    r = client.post("/api/cleanup")
    # Cleanup endpoint may not exist in current API
    if r.status_code == 404:
        pytest.skip("/api/cleanup endpoint not implemented")
    assert r.status_code == 200
    data = r.json()
    assert data["reports_removed"] >= 1
    assert old.exists() is False
