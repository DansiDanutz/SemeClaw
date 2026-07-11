"""Tests for the Digital Assistant routes (sessions, commands, memory, Twilio)."""

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
def sandbox(tmp_path):
    """Isolated storage + open writes + a fresh session table per test."""
    from war_room.dashboard.routes import assistant as ast
    from war_room.dashboard.routes import deps

    with (
        patch.object(ast, "TRANSCRIPTS_DIR", tmp_path / "transcripts"),
        patch.object(ast, "MEMORY_FILE", tmp_path / "memory" / "summaries.md"),
        patch.object(ast, "SESSIONS_DIR", tmp_path / "sessions"),
        patch.object(ast, "_sessions", {}),
        patch.object(deps, "SEMECLAW_RATE_LIMIT_PER_MIN", 0),
        patch.object(deps, "_RATE_BUCKETS", {}),
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
    ):
        yield ast


async def _fake_brain(model, messages):
    return "Sure — done."


async def _dead_brain(model, messages):
    return None


def test_config_endpoint(client):
    r = client.get("/api/assistant/config")
    assert r.status_code == 200
    data = r.json()
    assert data["name"]
    assert "ro" in data["languages"]
    assert data["calls_enabled"] in (True, False)


def test_assistant_page_served(client):
    r = client.get("/assistant")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Digital Assistant" in r.text


def test_message_requires_text(client, sandbox):
    assert client.post("/api/assistant/message", json={}).status_code == 400


def test_chat_turn_returns_reply_and_tts(client, sandbox):
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        r = client.post(
            "/api/assistant/message",
            json={"session_id": "t1", "text": "Hello there"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Sure — done."
    assert data["command"] is False
    assert data["tts"].startswith("/api/tts?text=")
    assert "lang=en" in data["tts"]


def test_lang_and_subject_commands_change_session(client, sandbox):
    r = client.post("/api/assistant/message", json={"session_id": "t2", "text": "/lang ro"})
    assert r.json()["lang"] == "ro"
    r = client.post("/api/assistant/message", json={"session_id": "t2", "text": "/subject confirm the demo"})
    data = r.json()
    assert data["command"] is True
    assert data["subject"] == "confirm the demo"

    # Session state feeds the system prompt (language + mission + memory)
    captured = {}

    async def spy(model, messages):
        captured["system"] = messages[0]["content"]
        return "Da, sigur."

    with (
        patch.object(sandbox, "_chat_openrouter", spy),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        r = client.post("/api/assistant/message", json={"session_id": "t2", "text": "salut"})
    assert r.status_code == 200
    assert "Romanian" in captured["system"]
    assert "confirm the demo" in captured["system"]
    assert "lang=ro" in r.json()["tts"]


def test_remember_and_recall(client, sandbox):
    r = client.post(
        "/api/assistant/message",
        json={"session_id": "t3", "text": "/remember Dan prefers Friday demos"},
    )
    assert "Saved" in r.json()["reply"]
    r = client.post("/api/assistant/message", json={"session_id": "t3", "text": "/recall friday demos"})
    assert "Dan prefers Friday demos" in r.json()["reply"]
    # memory endpoint too
    r = client.get("/api/assistant/memory?q=friday")
    assert "Dan prefers Friday demos" in r.json()["result"]


def test_end_archives_transcript_and_writes_memory(client, sandbox):
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        client.post("/api/assistant/message", json={"session_id": "t4", "text": "note this"})
        r = client.post("/api/assistant/end", json={"session_id": "t4"})
    assert r.status_code == 200
    data = r.json()
    assert data["summary"] == "Sure — done."
    files = list(sandbox.TRANSCRIPTS_DIR.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "note this" in body
    assert sandbox.MEMORY_FILE.exists()
    # ending again → 404
    assert client.post("/api/assistant/end", json={"session_id": "t4"}).status_code == 404


def test_call_503_without_twilio(client, sandbox):
    with patch.object(sandbox, "TWILIO_ACCOUNT_SID", ""):
        r = client.post("/api/assistant/call", json={"to": "+40712345678"})
    assert r.status_code == 503
    assert "Twilio" in r.json()["error"]


def test_twilio_answer_returns_gather_twiml(client, sandbox):
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
        patch.object(sandbox, "TWILIO_AUTH_TOKEN", ""),  # skip signature in dev mode
    ):
        r = client.post(
            "/api/assistant/twilio/answer?lang=ro&subject=demo&tenant=default",
            data={"CallSid": "CA123", "To": "+40712345678"},
        )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/xml")
    xml = r.text
    assert "<Gather" in xml and 'language="ro-RO"' in xml
    assert "Sure — done." in xml

    # follow-up turn keeps the conversation going
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
        patch.object(sandbox, "TWILIO_AUTH_TOKEN", ""),
    ):
        r = client.post(
            "/api/assistant/twilio/turn?tenant=default",
            data={"CallSid": "CA123", "To": "+40712345678", "SpeechResult": "Buna ziua"},
        )
    assert "<Gather" in r.text


def test_twilio_webhook_rejects_bad_signature(client, sandbox):
    with patch.object(sandbox, "TWILIO_AUTH_TOKEN", "tok123"):
        r = client.post(
            "/api/assistant/twilio/answer",
            data={"CallSid": "CA9", "To": "+1555"},
            headers={"X-Twilio-Signature": "bogus"},
        )
    assert r.status_code == 403


def test_sessions_listing_is_tenant_scoped(client, sandbox):
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        client.post(
            "/api/assistant/message",
            json={"session_id": "mine", "text": "hi"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
    r = client.get("/api/assistant/sessions", headers={"X-Tenant-Id": "tenant-a"})
    assert r.json()["count"] == 1
    r = client.get("/api/assistant/sessions", headers={"X-Tenant-Id": "tenant-b"})
    assert r.json()["count"] == 0


def test_message_bearer_gated_when_key_set(client, sandbox):
    with patch("war_room.dashboard.server.SEMECLAW_API_KEY", "sekret"):
        r = client.post("/api/assistant/message", json={"session_id": "x", "text": "hi"})
        assert r.status_code == 401


def test_inbound_call_answers_with_agent_persona(client, sandbox, tmp_path):
    from war_room.dashboard.routes import voice_agents as va

    with (
        patch.object(va, "VOICE_AGENTS_DIR", tmp_path / "voice_agents"),
        patch.object(sandbox, "TWILIO_AUTH_TOKEN", ""),
    ):
        client.post(
            "/api/voice-agents",
            json={
                "name": "Front Desk",
                "voice": "David",
                "greeting": "Hi, Front Desk here!",
                "knowledge": "Demos run Mon-Fri.",
            },
        )
        # inbound: agent's greeting is spoken inside a <Gather>
        r = client.post(
            "/api/assistant/twilio/inbound/front-desk?tenant=default",
            data={"CallSid": "CAIN1", "From": "+40711111111"},
        )
        assert r.status_code == 200
        assert "<Gather" in r.text
        assert "Hi, Front Desk here!" in r.text

        # follow-up turn goes through the AGENT prompt, not the assistant brain
        captured = {}

        async def spy(model, messages):
            captured["system"] = messages[0]["content"]
            return "Demos are on weekdays."

        with (
            patch.object(sandbox, "_chat_openrouter", spy),
            patch.object(sandbox, "_chat_ollama", _dead_brain),
        ):
            r = client.post(
                "/api/assistant/twilio/turn?tenant=default",
                data={"CallSid": "CAIN1", "To": "+1555", "SpeechResult": "When are demos?"},
            )
        assert "Demos are on weekdays." in r.text
        assert "Front Desk" in captured["system"]
        assert "Demos run Mon-Fri." in captured["system"]


def test_inbound_unknown_agent_hangs_up_politely(client, sandbox, tmp_path):
    from war_room.dashboard.routes import voice_agents as va

    with (
        patch.object(va, "VOICE_AGENTS_DIR", tmp_path / "voice_agents"),
        patch.object(sandbox, "TWILIO_AUTH_TOKEN", ""),
    ):
        r = client.post(
            "/api/assistant/twilio/inbound/ghost",
            data={"CallSid": "CAIN2", "From": "+1555"},
        )
    assert r.status_code == 200
    assert "<Hangup/>" in r.text


def test_inbound_rejects_bad_signature(client, sandbox):
    with patch.object(sandbox, "TWILIO_AUTH_TOKEN", "tok123"):
        r = client.post(
            "/api/assistant/twilio/inbound/any",
            data={"CallSid": "CAIN3"},
            headers={"X-Twilio-Signature": "bogus"},
        )
    assert r.status_code == 403


def test_sessions_survive_restart(client, sandbox):
    """Active conversations persist to disk and are restored after a restart."""
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        client.post("/api/assistant/message", json={"session_id": "keep", "text": "/lang ro"})
        client.post("/api/assistant/message", json={"session_id": "keep", "text": "remember the demo"})

    # Simulate a server restart: in-memory table wiped, disk remains
    sandbox._sessions.clear()

    captured = {}

    async def spy(model, messages):
        captured["messages"] = messages
        return "Da."

    with (
        patch.object(sandbox, "_chat_openrouter", spy),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        r = client.post("/api/assistant/message", json={"session_id": "keep", "text": "salut"})
    assert r.status_code == 200
    assert r.json()["lang"] == "ro"  # language restored from disk
    history = [m["content"] for m in captured["messages"]]
    assert "remember the demo" in history  # prior turns restored

    # /end works after the restart too, and removes the persisted file
    sandbox._sessions.clear()
    with (
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        r = client.post("/api/assistant/end", json={"session_id": "keep"})
    assert r.status_code == 200
    assert not list(sandbox.SESSIONS_DIR.glob("*.json"))


def test_message_rate_limited(client, sandbox):
    from war_room.dashboard.routes import deps

    with (
        patch.object(deps, "SEMECLAW_RATE_LIMIT_PER_MIN", 2),
        patch.object(deps, "_RATE_BUCKETS", {}),
        patch.object(sandbox, "_chat_openrouter", _fake_brain),
        patch.object(sandbox, "_chat_ollama", _dead_brain),
    ):
        assert client.post("/api/assistant/message", json={"text": "1"}).status_code == 200
        assert client.post("/api/assistant/message", json={"text": "2"}).status_code == 200
        r = client.post("/api/assistant/message", json={"text": "3"})
    assert r.status_code == 429
    assert "rate limit" in r.json()["error"]


def test_voice_agent_respond_rate_limited(client, sandbox, tmp_path):
    from war_room.dashboard.routes import deps
    from war_room.dashboard.routes import voice_agents as va

    async def agent_brain(model, messages):
        return "Hi."

    with (
        patch.object(va, "VOICE_AGENTS_DIR", tmp_path / "voice_agents"),
        patch.object(deps, "SEMECLAW_RATE_LIMIT_PER_MIN", 1),
        patch.object(deps, "_RATE_BUCKETS", {}),
        patch.object(va, "_chat_openrouter", agent_brain),
        patch.object(va, "_chat_ollama", _dead_brain),
    ):
        client.post("/api/voice-agents", json={"name": "RL Bot", "voice": "David"})
        assert client.post("/api/voice-agents/rl-bot/respond", json={"message": "a"}).status_code == 200
        r = client.post("/api/voice-agents/rl-bot/respond", json={"message": "b"})
    assert r.status_code == 429
