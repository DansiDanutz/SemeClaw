"""Tests for the Voice Agent Builder routes (CRUD + respond + UI page)."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

os.chdir(str(ROOT))

from war_room.dashboard.server import app  # noqa: E402


@pytest.fixture
def client():
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def tenant_headers(tmp_path):
    """Give each test its own tenant so agent files never collide, point
    storage at a temp dir so the repo tree stays clean, and clear the API
    key (other test modules may leave it set) so writes are open by default."""
    from war_room.dashboard.routes import voice_agents as va

    with (
        patch.object(va, "VOICE_AGENTS_DIR", tmp_path / "voice_agents"),
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
        patch("war_room.dashboard.server._is_loopback_request", return_value=True),
    ):
        yield {"X-Tenant-Id": "pytest-tenant"}


def test_voices_endpoint_lists_builder_voices(client):
    r = client.get("/api/voice-agents/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert any(v["speaker"] == "David" for v in voices)
    assert all({"speaker", "description"} <= set(v) for v in voices)


def test_create_get_list_delete_roundtrip(client, tenant_headers):
    r = client.post(
        "/api/voice-agents",
        json={"name": "Front Desk", "voice": "David", "persona": "Friendly."},
        headers=tenant_headers,
    )
    assert r.status_code == 201
    agent = r.json()
    assert agent["id"] == "front-desk"
    assert agent["greeting"]  # default greeting filled in

    r = client.get("/api/voice-agents/front-desk", headers=tenant_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Front Desk"

    r = client.get("/api/voice-agents", headers=tenant_headers)
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # Re-post with same name = update, not duplicate
    r = client.post(
        "/api/voice-agents",
        json={"name": "Front Desk", "voice": "Sienna"},
        headers=tenant_headers,
    )
    assert r.status_code == 200
    assert r.json()["voice"] == "Sienna"
    assert client.get("/api/voice-agents", headers=tenant_headers).json()["count"] == 1

    r = client.delete("/api/voice-agents/front-desk", headers=tenant_headers)
    assert r.status_code == 200
    assert client.get("/api/voice-agents/front-desk", headers=tenant_headers).status_code == 404


def test_create_rejects_missing_name_and_bad_voice(client, tenant_headers):
    assert client.post("/api/voice-agents", json={}, headers=tenant_headers).status_code == 400
    r = client.post("/api/voice-agents", json={"name": "X", "voice": "NotAVoice"}, headers=tenant_headers)
    assert r.status_code == 400
    assert "available" in r.json()


def test_respond_unknown_agent_404(client, tenant_headers):
    r = client.post("/api/voice-agents/ghost/respond", json={"message": "hi"}, headers=tenant_headers)
    assert r.status_code == 404


def test_respond_returns_reply_and_tts_url(client, tenant_headers):
    from war_room.dashboard.routes import voice_agents as va

    client.post(
        "/api/voice-agents",
        json={"name": "Pizza Line", "voice": "Memo", "knowledge": "Margherita is 9 EUR."},
        headers=tenant_headers,
    )

    async def fake_openrouter(model, messages):
        # The system prompt must carry the agent's knowledge + persona
        assert messages[0]["role"] == "system"
        assert "Margherita is 9 EUR." in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "How much is a margherita?"}
        return "A margherita is nine euros."

    with patch.object(va, "_chat_openrouter", fake_openrouter):
        r = client.post(
            "/api/voice-agents/pizza-line/respond",
            json={"message": "How much is a margherita?"},
            headers=tenant_headers,
        )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "A margherita is nine euros."
    assert data["voice"] == "Memo"
    assert data["tts"].startswith("/api/tts?text=")


def test_respond_503_when_all_models_down(client, tenant_headers):
    from war_room.dashboard.routes import voice_agents as va

    client.post("/api/voice-agents", json={"name": "Down Bot", "voice": "David"}, headers=tenant_headers)

    async def dead(model, messages):
        return None

    with patch.object(va, "_chat_openrouter", dead), patch.object(va, "_chat_ollama", dead):
        r = client.post(
            "/api/voice-agents/down-bot/respond",
            json={"message": "hello"},
            headers=tenant_headers,
        )
    assert r.status_code == 503


def test_builder_page_served(client):
    r = client.get("/voice-builder")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Voice Agent Builder" in r.text


def test_manifest_advertises_voice_agents(client):
    data = client.get("/api/agent/manifest").json()
    assert "voice.agents.builder" in data["capabilities"]
    assert data["endpoints"]["voice_builder_ui"] == "/voice-builder"


def test_write_endpoints_require_bearer_when_key_set(client, tenant_headers):
    import war_room.dashboard.server as srv

    with patch.object(srv, "SEMECLAW_API_KEY", "sekret"):
        r = client.post("/api/voice-agents", json={"name": "Nope"}, headers=tenant_headers)
        assert r.status_code == 401
        r = client.post(
            "/api/voice-agents",
            json={"name": "Yep", "voice": "David"},
            headers={**tenant_headers, "Authorization": "Bearer sekret"},
        )
        assert r.status_code in (200, 201)
        # reads stay open
        assert client.get("/api/voice-agents", headers=tenant_headers).status_code == 200
