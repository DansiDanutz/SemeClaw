"""Tests for agent manifest, embed endpoints, and bearer auth."""

import json
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
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_manifest_returns_contract(client):
    r = client.get("/api/agent/manifest")
    assert r.status_code == 200
    data = r.get_json()
    assert data["agent"]["name"] == "SemeClaw"
    assert "capabilities" in data
    assert "endpoints" in data
    assert data["auth"]["type"] == "bearer"
    assert data["auth"]["public_reads"] is True


def test_embed_js_returns_script(client):
    r = client.get("/embed.js")
    assert r.status_code == 200
    assert r.content_type.startswith("application/javascript")
    body = r.data.decode()
    assert "initSemeClaw" in body
    assert "data-semeclaw-meeting" in body


def test_embed_returns_iframe_html(client):
    r = client.get("/embed?meeting=test.md&v=2&theme=dark")
    assert r.status_code == 200
    assert r.content_type.startswith("text/html")
    body = r.data.decode()
    assert "iframe" in body
    assert "test.md" in body
    assert "Content-Security-Policy" in r.headers


def test_write_endpoints_open_when_no_api_key(client):
    """If SEMECLAW_API_KEY is unset, writes should succeed without auth."""
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""):
        r = client.post("/api/meeting/pin", json={"name": "x.md"})
        assert r.status_code == 200


def test_write_endpoints_require_bearer_when_key_set(client):
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", "secret123"):
        # No auth header
        r = client.post("/api/meeting/pin", json={"name": "x.md"})
        assert r.status_code == 401
        assert "Bearer" in r.get_json()["error"]

        # Wrong token
        r = client.post(
            "/api/meeting/pin",
            json={"name": "x.md"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

        # Correct token
        r = client.post(
            "/api/meeting/pin",
            json={"name": "x.md"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert r.status_code == 200


def test_read_endpoints_always_public(client):
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", "secret123"):
        r = client.get("/api/agent/manifest")
        assert r.status_code == 200

        r = client.get("/api/tts/health")
        assert r.status_code == 200

        r = client.get("/embed.js")
        assert r.status_code == 200
