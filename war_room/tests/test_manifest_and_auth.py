"""Tests for agent manifest, embed endpoints, and bearer auth."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.requests import Request

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

os.chdir(str(ROOT))

from war_room.dashboard import server

app = server.app


def _request(
    *,
    client_host: str = "127.0.0.1",
    host: str = "127.0.0.1:8765",
    forwarded_for: str | None = None,
) -> Request:
    headers = [(b"host", host.encode())]
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/run",
            "raw_path": b"/api/run",
            "query_string": b"",
            "headers": headers,
            "client": (client_host, 12345),
            "server": ("127.0.0.1", 8765),
        }
    )


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
    assert data["auth"]["required_for_remote_writes"] is True
    assert data["auth"]["protected_paths"] == ["/api/* (POST, PUT, PATCH, DELETE)"]
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


def test_loopback_exception_requires_local_deployment_and_direct_request():
    with patch("war_room.dashboard.server.SEMECLAW_PUBLIC_URL", "http://127.0.0.1:8765"):
        assert server._is_loopback_request(_request()) is True
        assert server._is_loopback_request(_request(host="semeclaw.example")) is False
        assert server._is_loopback_request(_request(forwarded_for="198.51.100.8")) is False

    with patch("war_room.dashboard.server.SEMECLAW_PUBLIC_URL", "https://semeclaw.example"):
        assert server._is_loopback_request(_request()) is False


def test_rate_limit_client_ip_uses_only_explicitly_trusted_proxy_chain():
    request = _request(client_host="127.0.0.1", forwarded_for="198.51.100.8")
    with patch("war_room.dashboard.server._TRUSTED_PROXY_NETWORKS", ()):
        assert server._rate_limit_client_ip(request) == "127.0.0.1"

    trusted = server._parse_trusted_proxy_networks("127.0.0.0/8,10.0.0.0/8")
    request = _request(
        client_host="127.0.0.1",
        forwarded_for="192.0.2.44, 198.51.100.8, 10.1.2.3",
    )
    with patch("war_room.dashboard.server._TRUSTED_PROXY_NETWORKS", trusted):
        assert server._rate_limit_client_ip(request) == "198.51.100.8"


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


def test_public_spotlight_click_reaches_validation_without_global_key(client):
    with (
        patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
        patch("war_room.dashboard.server._is_loopback_request", return_value=False),
    ):
        assert client.post("/api/spotlight/click", json={}).status_code == 400


def test_public_spotlight_click_is_rate_limited(client):
    limit = server._RATE_LIMIT_BY_PREFIX["/api/spotlight/click"]
    server._RATE_WINDOWS.clear()
    try:
        with (
            patch("war_room.dashboard.server.SEMECLAW_API_KEY", ""),
            patch("war_room.dashboard.server._is_loopback_request", return_value=False),
        ):
            for _ in range(limit):
                assert client.post("/api/spotlight/click", json={}).status_code == 400

            response = client.post("/api/spotlight/click", json={})

        assert response.status_code == 429
        assert response.json()["error"] == "rate_limit_exceeded"
        assert response.headers["retry-after"] == str(server._RATE_LIMIT_WINDOW)
    finally:
        server._RATE_WINDOWS.clear()


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
