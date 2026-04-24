"""Tests for the extended /metrics endpoint + prom counter wiring."""

from __future__ import annotations

import importlib
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    return TestClient(srv.app)


def test_metrics_has_legacy_and_prom_client(client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    # Legacy (hand-rolled) counters added by Dan
    assert "semeclaw_info" in body
    assert "semeclaw_meetings_started" in body
    # Our new prometheus_client registry appended
    assert "semeclaw_impressions_total" in body
    assert "semeclaw_ws_connections_active" in body
    assert "semeclaw_http_requests_total" in body


def test_http_counter_increments(client) -> None:
    client.get("/api/agent/manifest")  # seed a request
    body = client.get("/metrics").text
    m = re.search(
        r'semeclaw_http_requests_total\{method="GET",status_class="2xx"\} ([\d.]+)',
        body,
    )
    assert m and float(m.group(1)) >= 1.0


def test_metrics_endpoint_is_public(client) -> None:
    r = client.get("/metrics", headers={"Authorization": ""})
    assert r.status_code == 200


def test_ws_manager_hooks_do_not_raise_without_client() -> None:
    """Even if prometheus_client isn't installed, ConnectionManager must work."""
    import war_room.dashboard.websocket_manager as wm

    importlib.reload(wm)
    cm = wm.ConnectionManager()
    # These call metrics.inc() through _bump_gauge — must not raise
    wm._bump_gauge(1)
    wm._bump_gauge(-1)
    wm._count_broadcast("meeting_message")
