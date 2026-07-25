"""Tests for agent manifest, embed endpoints, and bearer auth."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

os.chdir(str(ROOT))

from war_room.dashboard.server import app


@pytest.fixture
def client():
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c


def test_manifest_returns_contract(client):
    r = client.get("/api/agent/manifest")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "SemeClaw War Room"
    assert "capabilities" in data
    assert "endpoints" in data
    assert "capabilities" in data
    assert "endpoints" in data


def test_embed_js_returns_script(client):
    r = client.get("/embed.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    body = r.text
    assert "SemeClaw" in body
    assert "data-semeclaw-meeting" in body


def test_embed_returns_iframe_html(client):
    r = client.get("/embed?meeting=test.md&v=2&theme=dark")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "War Room" in body or "war-room" in body.lower()
    assert "Content-Security-Policy" in r.headers


def test_write_endpoints_open_on_loopback_when_no_api_key(client):
    with (
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
        patch("war_room.dashboard.server._is_loopback_request", return_value=True),
        patch("war_room.dashboard.server._build_meeting_mp3", return_value=None),
    ):
        r = client.post("/api/meeting/pin", params={"name": "x.md"})
        # _build_meeting_mp3 returns None for missing report → 500
        assert r.status_code in (200, 500)


def test_write_endpoints_fail_closed_off_loopback_without_api_key(client):
    with (
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
        patch("war_room.dashboard.server._is_loopback_request", return_value=False),
    ):
        r = client.post("/api/meeting/pin", params={"name": "x.md"})
        assert r.status_code == 503
        assert "required" in r.json()["error"]


def test_unlisted_write_endpoint_still_requires_bearer(client):
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", "secret123"):
        assert client.post("/api/run", json={}).status_code == 401


def test_write_endpoints_require_bearer_when_key_set(client):
    with (
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", "secret123"),
        patch("war_room.dashboard.server._build_meeting_mp3", return_value=None),
    ):
        # No auth header
        r = client.post("/api/meeting/pin", params={"name": "x.md"})
        assert r.status_code == 401
        assert r.json()["error"] in ("unauthorized", "Bearer token required")

        # Wrong token
        r = client.post(
            "/api/meeting/pin",
            params={"name": "x.md"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

        # Correct token — _build_meeting_mp3 returns None → 500, auth passed though
        r = client.post(
            "/api/meeting/pin",
            params={"name": "x.md"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert r.status_code in (200, 500)


def test_read_endpoints_always_public(client):
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", "secret123"):
        r = client.get("/api/agent/manifest")
        assert r.status_code == 200

        r = client.get("/api/agent/health")
        assert r.status_code == 200

        r = client.get("/embed.js")
        assert r.status_code == 200
