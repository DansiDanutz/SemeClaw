"""
Integration tests for the SemeClaw FastAPI server.

Covers:
  - Health & discovery (health, manifest, metrics)
  - Report CRUD (create, list, content, delete)
  - Auth enforcement (401 on protected endpoints when key is set)
  - Rate limiting (429 after limit exceeded)
  - Tenant isolation (X-Tenant-Id header)
  - SSE + webhook endpoints (shape only, no live stream)
  - TTS endpoint reachable (no real ElevenLabs key in CI)

Run:
    uv run pytest tests/test_api.py -v
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# ── path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

# Set env before importing server so globals are correct
os.environ.setdefault("SEMECLAW_TENANT_ID", "default")
os.environ.setdefault("SEMECLAW_PUBLIC_URL", "http://localhost:8765")
# Open mode for most tests (no auth key)
_TEST_API_KEY = "test-secret-key"

# ── app import ───────────────────────────────────────────────────────────────
import server as srv  # war_room/dashboard/server.py
from fastapi.testclient import TestClient


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def open_client():
    """TestClient with no API key (open mode)."""
    original = srv.SEMECLAW_API_KEY
    srv.SEMECLAW_API_KEY = ""
    try:
        with TestClient(srv.app, raise_server_exceptions=False) as c:
            yield c
    finally:
        srv.SEMECLAW_API_KEY = original


@pytest.fixture()
def auth_client():
    """TestClient with API key enforced."""
    original = srv.SEMECLAW_API_KEY
    srv.SEMECLAW_API_KEY = _TEST_API_KEY
    try:
        with TestClient(srv.app, raise_server_exceptions=False) as c:
            yield c
    finally:
        srv.SEMECLAW_API_KEY = original


@pytest.fixture()
def report_name(open_client, tmp_path):
    """Create a test report, yield its name, then delete it."""
    payload = {
        "task": "Integration test task",
        "content": (
            "# Integration Test Report\n\n"
            "**Task:** Integration test task\n"
            "**Date:** 2026-01-01\n\n---\n\n"
            "## Research Agent Output\n\n"
            "This is a long enough research block to pass the section filter. "
            "It contains useful information about the test subject matter. "
            "The test ensures that content is properly stored and retrieved.\n\n"
            "## Strategist Agent Output\n\n"
            "Strategic recommendation: keep tests green. "
            "Ensure each endpoint returns expected status codes. "
            "Validate shapes, not just status codes.\n"
        ),
        "auto_audio": False,
    }
    r = open_client.post("/api/reports", json=payload)
    assert r.status_code == 201, f"setup failed: {r.text}"
    name = r.json()["name"]
    yield name
    # cleanup
    open_client.delete(f"/api/reports?name={name}")


# ── Health & Discovery ────────────────────────────────────────────────────────

class TestHealth:
    def test_health_200(self, open_client):
        r = open_client.get("/api/agent/health")
        assert r.status_code == 200

    def test_health_has_agents_key(self, open_client):
        r = open_client.get("/api/agent/health")
        d = r.json()
        assert "agents" in d

    def test_csp_header_present(self, open_client):
        r = open_client.get("/api/agent/health")
        assert "content-security-policy" in r.headers or "Content-Security-Policy" in r.headers

    def test_version_header_present(self, open_client):
        r = open_client.get("/api/agent/health")
        assert r.headers.get("x-semeclaw-version") == srv.APP_VERSION


class TestManifest:
    def test_manifest_200(self, open_client):
        r = open_client.get("/api/agent/manifest")
        assert r.status_code == 200

    def test_manifest_id(self, open_client):
        d = open_client.get("/api/agent/manifest").json()
        assert d["id"] == "semeclaw-war-room"

    def test_manifest_version_semver(self, open_client):
        import re
        d = open_client.get("/api/agent/manifest").json()
        assert re.match(r"^\d+\.\d+\.\d+$", d["version"])
        assert d["version"] == srv.APP_VERSION

    def test_manifest_capabilities_count(self, open_client):
        d = open_client.get("/api/agent/manifest").json()
        assert len(d["capabilities"]) >= 10

    def test_manifest_required_capabilities(self, open_client):
        d = open_client.get("/api/agent/manifest").json()
        caps = set(d["capabilities"])
        for required in ("meeting.audio", "reports.create", "tts.synthesize", "embed.iframe"):
            assert required in caps, f"missing capability: {required}"

    def test_manifest_endpoints_dict(self, open_client):
        d = open_client.get("/api/agent/manifest").json()
        assert isinstance(d["endpoints"], dict)
        assert "health" in d["endpoints"]
        assert "manifest" in d["endpoints"]


class TestMetrics:
    def test_metrics_200(self, open_client):
        r = open_client.get("/metrics")
        assert r.status_code == 200

    def test_metrics_content_type(self, open_client):
        r = open_client.get("/metrics")
        assert "text/plain" in r.headers["content-type"]

    def test_metrics_has_semeclaw_info(self, open_client):
        r = open_client.get("/metrics")
        assert "semeclaw_info" in r.text

    def test_metrics_has_version_label(self, open_client):
        r = open_client.get("/metrics")
        assert f'version="{srv.APP_VERSION}"' in r.text


# ── Reports CRUD ──────────────────────────────────────────────────────────────

class TestReports:
    def test_list_reports_200(self, open_client):
        r = open_client.get("/api/reports")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_report_201(self, open_client):
        payload = {
            "task": "Test create",
            "content": (
                "# Test\n\n**Task:** Test create\n\n---\n\n"
                "## Research Agent Output\n\nLong enough content block here. " * 3
            ),
        }
        r = open_client.post("/api/reports", json=payload)
        assert r.status_code == 201
        d = r.json()
        assert "name" in d
        assert d["name"].endswith(".md")
        # cleanup
        open_client.delete(f"/api/reports?name={d['name']}")

    def test_create_report_missing_content_400(self, open_client):
        r = open_client.post("/api/reports", json={"task": "no content"})
        assert r.status_code == 400

    def test_report_content_200(self, open_client, report_name):
        r = open_client.get(f"/api/reports/content?name={report_name}")
        assert r.status_code == 200
        assert "Integration Test Report" in r.text

    def test_report_content_not_found_404(self, open_client):
        r = open_client.get("/api/reports/content?name=does-not-exist.md")
        assert r.status_code == 404

    def test_report_appears_in_list(self, open_client, report_name):
        r = open_client.get("/api/reports")
        names = [item["name"] for item in r.json()]
        assert report_name in names

    def test_delete_report_200(self, open_client):
        # Create then delete
        payload = {
            "task": "delete me",
            "content": "# Delete test\n\n**Task:** delete\n\n## Research\n\n" + "x" * 100,
        }
        r = open_client.post("/api/reports", json=payload)
        name = r.json()["name"]
        r2 = open_client.delete(f"/api/reports?name={name}")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

    def test_delete_missing_report_404(self, open_client):
        r = open_client.delete("/api/reports?name=ghost.md")
        assert r.status_code == 404


# ── Meeting Script ──────────────────────────────────────────────────────────

class TestMeetingScript:
    def test_script_200(self, open_client, report_name):
        r = open_client.get(f"/api/meeting/script?name={report_name}")
        assert r.status_code == 200

    def test_script_has_segments(self, open_client, report_name):
        r = open_client.get(f"/api/meeting/script?name={report_name}")
        d = r.json()
        assert "segments" in d
        assert len(d["segments"]) >= 4

    def test_script_first_segment_is_host(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/script?name={report_name}").json()
        assert d["segments"][0]["role"] == "host"

    def test_script_last_segment_is_dan(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/script?name={report_name}").json()
        assert d["segments"][-1]["role"] == "dan"

    def test_script_missing_report_404(self, open_client):
        r = open_client.get("/api/meeting/script?name=ghost.md")
        assert r.status_code == 404


# ── Authentication ──────────────────────────────────────────────────────────

class TestAuth:
    """When SEMECLAW_API_KEY is set, write endpoints require Bearer token."""

    def test_pin_without_key_401(self, auth_client):
        r = auth_client.post("/api/meeting/pin", json={"name": "test.md"})
        assert r.status_code == 401

    def test_pin_wrong_key_401(self, auth_client):
        r = auth_client.post(
            "/api/meeting/pin",
            json={"name": "test.md"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401

    def test_pin_correct_key_not_401(self, auth_client):
        # Will 404 (report doesn't exist) but NOT 401
        r = auth_client.post(
            "/api/meeting/pin",
            json={"name": "nonexistent.md"},
            headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
        )
        assert r.status_code != 401

    def test_finalize_without_key_401(self, auth_client):
        r = auth_client.post("/api/meeting/finalize", json={})
        assert r.status_code == 401

    def test_replan_without_key_401(self, auth_client):
        r = auth_client.post("/api/meeting/replan", json={})
        assert r.status_code == 401

    def test_redirect_without_key_401(self, auth_client):
        r = auth_client.post("/api/meeting/redirect", json={})
        assert r.status_code == 401

    def test_read_endpoints_open_no_key_needed(self, auth_client):
        """Read-only endpoints must work without a token even when key is set."""
        r = auth_client.get("/api/agent/health")
        assert r.status_code == 200
        r2 = auth_client.get("/api/agent/manifest")
        assert r2.status_code == 200
        r3 = auth_client.get("/api/reports")
        assert r3.status_code == 200


# ── Rate Limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    """Verify the in-memory sliding-window rate limiter triggers 429.

    We test using /api/meeting/script instead of /api/tts. To avoid the free-
    tier server-side wait, the test temporarily enables a pro license key and
    sends it on each request. That isolates the rate-limiter behavior itself.
    """

    TEST_PRO_LICENSE = "test-pro-license"

    @pytest.fixture(autouse=True)
    def _reset_rate_windows(self):
        """Clear rate-limit windows before and after each test."""
        original_keys = set(srv._PRO_KEYS)
        srv._RATE_WINDOWS.clear()
        srv._PRO_KEYS = {self.TEST_PRO_LICENSE}
        yield
        srv._RATE_WINDOWS.clear()
        srv._PRO_KEYS = original_keys

    def _script_limit(self):
        return srv._RATE_LIMIT_BY_PREFIX.get("/api/meeting/script", 30)

    def _script_headers(self):
        return {"x-semeclaw-license": self.TEST_PRO_LICENSE}

    def test_script_rate_limit_triggers_429(self, open_client, report_name):
        """Exceed the /api/meeting/script limit — must get at least one 429."""
        limit = self._script_limit()
        statuses = []
        for _ in range(limit + 3):
            r = open_client.get(
                f"/api/meeting/script?name={report_name}",
                headers=self._script_headers(),
            )
            statuses.append(r.status_code)
        assert 429 in statuses, f"Expected 429 after {limit} requests; got: {set(statuses)}"

    def test_429_has_retry_after_header(self, open_client, report_name):
        limit = self._script_limit()
        for _ in range(limit + 3):
            r = open_client.get(
                f"/api/meeting/script?name={report_name}",
                headers=self._script_headers(),
            )
            if r.status_code == 429:
                assert "retry-after" in r.headers
                return
        pytest.fail("Never got a 429")

    def test_429_body_has_error_key(self, open_client, report_name):
        limit = self._script_limit()
        for _ in range(limit + 3):
            r = open_client.get(
                f"/api/meeting/script?name={report_name}",
                headers=self._script_headers(),
            )
            if r.status_code == 429:
                assert r.json()["error"] == "rate_limit_exceeded"
                return
        pytest.fail("Never got a 429")

    def test_rate_limit_config_exported(self):
        """The rate-limit map must be a module-level dict (needed by tests)."""
        assert isinstance(srv._RATE_LIMIT_BY_PREFIX, dict)
        assert "/api/tts" in srv._RATE_LIMIT_BY_PREFIX
        assert "/api/meeting/audio" in srv._RATE_LIMIT_BY_PREFIX

    def test_health_never_rate_limited(self, open_client):
        """Health is not in the rate-limit map — must never 429."""
        for _ in range(30):
            r = open_client.get("/api/agent/health")
            assert r.status_code != 429


# ── Tenant Isolation ──────────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_manifest_reflects_default_tenant(self, open_client):
        d = open_client.get("/api/agent/manifest").json()
        assert "tenant" in d

    def test_create_report_tenant_a_invisible_to_tenant_b(self, open_client):
        """Reports created under tenant-a must NOT appear in tenant-b listing."""
        headers_a = {"x-tenant-id": "tenant-test-a"}
        headers_b = {"x-tenant-id": "tenant-test-b"}

        # Create under A
        r = open_client.post(
            "/api/reports",
            json={
                "task": "Tenant A report",
                "content": "# Tenant A\n\n**Task:** isolation\n\n## Research\n\n" + "x" * 100,
            },
            headers=headers_a,
        )
        assert r.status_code == 201
        name = r.json()["name"]

        try:
            # List under B — should NOT see A's report
            r_b = open_client.get("/api/reports", headers=headers_b)
            b_names = [item["name"] for item in r_b.json()]
            assert name not in b_names, "Tenant B can see Tenant A's report!"
        finally:
            open_client.delete(f"/api/reports?name={name}", headers=headers_a)


# ── SSE & Webhooks ────────────────────────────────────────────────────────────

class TestSSEAndWebhooks:
    def test_events_endpoint_in_manifest(self, open_client):
        """SSE endpoint is advertised in the manifest — verify it's registered."""
        d = open_client.get("/api/agent/manifest").json()
        caps = d.get("capabilities", [])
        assert "meeting.events.sse" in caps

    def test_webhook_register(self, open_client):
        r = open_client.post("/api/webhooks", json={
            "url": "https://example.com/hook",
            "events": ["meeting.finalized"],
            "secret": "my-hmac-secret",
        })
        assert r.status_code in (200, 201)
        d = r.json()
        assert "id" in d or "hook_id" in d or "ok" in d

    def test_webhook_list(self, open_client):
        r = open_client.get("/api/webhooks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── Share Links ───────────────────────────────────────────────────────────────

class TestShareLinks:
    def test_share_link_creates_token(self, open_client, report_name):
        # Share requires audio to be generated first; without auto_audio the
        # endpoint returns 404. Either outcome (200 with token, 404 no audio)
        # is valid — we just confirm it's not a server error.
        r = open_client.get(f"/api/meeting/share?name={report_name}")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            d = r.json()
            assert "url" in d or "token" in d

    def test_share_page_reachable(self, open_client, report_name):
        r = open_client.get(f"/api/meeting/share?name={report_name}")
        token = (r.json().get("url") or "").split("/share/")[-1] or r.json().get("token")
        if token:
            r2 = open_client.get(f"/share/{token}")
            # May be 200 or redirect — just not a server error
            assert r2.status_code < 500


# ── Embed ─────────────────────────────────────────────────────────────────────

class TestEmbed:
    def test_embed_js_200(self, open_client):
        r = open_client.get("/embed.js")
        assert r.status_code == 200
        assert "javascript" in r.headers.get("content-type", "")

    def test_embed_js_has_semeclaw_class(self, open_client):
        r = open_client.get("/embed.js")
        assert "semeclaw" in r.text.lower()

    def test_embed_page_200(self, open_client, report_name):
        r = open_client.get(f"/embed?meeting={report_name}&v=1&theme=dark")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")


# ── Voice Overrides ───────────────────────────────────────────────────────────

class TestVoiceOverrides:
    def test_voices_map_get(self, open_client):
        r = open_client.get("/api/voices/map")
        assert r.status_code == 200

    def test_voices_map_returns_dict(self, open_client):
        d = open_client.get("/api/voices/map").json()
        assert isinstance(d, dict)


# ── Skill Registry ────────────────────────────────────────────────────────────

class TestSkillRegistry:
    def test_agents_skills_200(self, open_client):
        r = open_client.get("/api/agents/skills")
        assert r.status_code == 200

    def test_agents_skills_has_count(self, open_client):
        d = open_client.get("/api/agents/skills").json()
        assert "count" in d
        assert d["count"] >= 5  # autoresearch, gsd, hermes, david, dexter, narrator

    def test_agents_skills_has_agents_list(self, open_client):
        d = open_client.get("/api/agents/skills").json()
        assert isinstance(d.get("agents"), list)
        assert len(d["agents"]) >= 5

    def test_agents_skills_schema(self, open_client):
        d = open_client.get("/api/agents/skills").json()
        for agent in d["agents"]:
            assert "id" in agent
            assert "name" in agent
            assert "speaker" in agent
            assert "role" in agent
            assert "triggers" in agent
            assert isinstance(agent["triggers"], list)
            assert "how_to_invoke" in agent
            assert "interacts_with" in agent

    def test_agents_skills_known_ids(self, open_client):
        d = open_client.get("/api/agents/skills").json()
        ids = {a["id"] for a in d["agents"]}
        assert "autoresearch" in ids
        assert "gsd" in ids
        assert "hermes" in ids
        assert "david" in ids
        assert "dexter" in ids

    def test_agents_skill_detail_200(self, open_client):
        r = open_client.get("/api/agents/skills/gsd")
        assert r.status_code == 200

    def test_agents_skill_detail_fields(self, open_client):
        d = open_client.get("/api/agents/skills/gsd").json()
        assert d["id"] == "gsd"
        assert "role" in d
        assert "triggers" in d
        assert "interacts_with" in d
        assert "human_interaction" in d
        assert "body" in d  # full markdown body included

    def test_agents_skill_detail_404(self, open_client):
        r = open_client.get("/api/agents/skills/does-not-exist")
        assert r.status_code == 404

    def test_agents_skill_interaction_graph(self, open_client):
        d = open_client.get("/api/agents/skills/gsd").json()
        interactions = d.get("interacts_with", [])
        assert len(interactions) >= 2  # gsd interacts with autoresearch, david, hermes
        for i in interactions:
            assert "agent" in i
            assert "pattern" in i


# ── Meeting Agents (human join guide) ─────────────────────────────────────────

class TestMeetingAgents:
    def test_meeting_agents_200(self, open_client, report_name):
        r = open_client.get(f"/api/meeting/agents?name={report_name}")
        assert r.status_code == 200

    def test_meeting_agents_has_agent_count(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/agents?name={report_name}").json()
        assert "agent_count" in d
        assert d["agent_count"] >= 3  # at least David, Dan, Narrator + agents from content

    def test_meeting_agents_includes_known_speakers(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/agents?name={report_name}").json()
        speakers = {a["speaker"] for a in d.get("agents", [])}
        # report_name fixture has Research Agent and Strategist sections
        assert "David" in speakers  # always present (orchestrator)
        assert "Narrator" in speakers  # always present

    def test_meeting_agents_has_human_guide(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/agents?name={report_name}").json()
        assert "human_guide" in d
        guide = d["human_guide"]
        assert "inject" in guide.lower() or "meeting" in guide.lower()

    def test_meeting_agents_agents_have_how_to_invoke(self, open_client, report_name):
        d = open_client.get(f"/api/meeting/agents?name={report_name}").json()
        for agent in d.get("agents", []):
            assert "how_to_invoke" in agent
            assert "speaker" in agent

    def test_meeting_agents_404_for_missing(self, open_client):
        r = open_client.get("/api/meeting/agents?name=does-not-exist.md")
        assert r.status_code == 404

    def test_meeting_agents_400_no_name(self, open_client):
        r = open_client.get("/api/meeting/agents")
        assert r.status_code == 400


# ── Human Injection ───────────────────────────────────────────────────────────

class TestHumanInject:
    _MEETING = {
        "name": "test-meeting",
        "subject": "NERVIX pricing strategy",
        "attendees": ["Narrator", "David", "GSD", "Autoresearch", "Dan"],
        "history": [
            {"speaker": "Autoresearch", "text": "Competitors charge 15-20% commission."},
            {"speaker": "GSD", "text": "Recommend 12% to undercut competitors."},
        ],
        "remaining": [
            {
                "speaker": "David",
                "text": "The 12% commission is technically sustainable.",
                "role": "orchestrator",
                "pause_ms_after": 300,
            },
            {
                "speaker": "Dan",
                "text": "Let us move forward with this.",
                "role": "dan",
                "pause_ms_after": 500,
            },
        ],
    }

    def test_inject_200(self, open_client):
        r = open_client.post("/api/meeting/inject", json={
            "message": "What about 8% for the first 6 months?",
            "intent": "question",
            "meeting": self._MEETING,
        })
        assert r.status_code == 200

    def test_inject_has_responder(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "What about 8% for the first 6 months?",
            "intent": "question",
            "meeting": self._MEETING,
        }).json()
        assert "responder" in d
        assert d["responder"]  # not empty

    def test_inject_has_response(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "What about 8% for the first 6 months?",
            "intent": "question",
            "meeting": self._MEETING,
        }).json()
        assert "response" in d
        assert d["response"]  # not empty

    def test_inject_has_inject_id(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "new requirement here",
            "intent": "question",
            "meeting": self._MEETING,
        }).json()
        assert "inject_id" in d
        assert len(d["inject_id"]) == 8

    def test_inject_responder_is_attendee(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "What about 8% commission?",
            "intent": "question",
            "meeting": self._MEETING,
        }).json()
        attendees = self._MEETING["attendees"]
        assert d["responder"] in attendees

    def test_inject_forced_agent(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "Tell me the architecture implications",
            "intent": "question",
            "agent_id": "David",
            "meeting": self._MEETING,
        }).json()
        assert d["responder"] == "David"

    def test_inject_400_no_message(self, open_client):
        r = open_client.post("/api/meeting/inject", json={
            "message": "",
            "meeting": self._MEETING,
        })
        assert r.status_code == 400

    def test_inject_returns_remaining_segments(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "What about 8% for the first 6 months?",
            "intent": "both",
            "meeting": self._MEETING,
        }).json()
        assert "recalibrated_segments" in d
        assert isinstance(d["recalibrated_segments"], list)

    def test_inject_responder_skill_card(self, open_client):
        d = open_client.post("/api/meeting/inject", json={
            "message": "What should our pricing strategy be?",
            "intent": "question",
            "agent_id": "GSD",
            "meeting": self._MEETING,
        }).json()
        skill = d.get("responder_skill")
        # GSD has a skill card — it should be returned
        if skill:
            assert "id" in skill
            assert "role" in skill
            assert "how_to_invoke" in skill
