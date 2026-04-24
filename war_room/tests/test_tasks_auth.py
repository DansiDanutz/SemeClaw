"""Regression: /api/tasks write endpoints must be bearer-gated."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_key(monkeypatch):
    monkeypatch.setenv("SEMECLAW_API_KEY", "test-bearer-semeclaw")
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    return TestClient(srv.app), "test-bearer-semeclaw"


def test_task_create_rejected_without_bearer(client_with_key) -> None:
    client, _ = client_with_key
    r = client.post("/api/tasks", json={"title": "x"})
    assert r.status_code == 401


def test_task_gc_rejected_without_bearer(client_with_key) -> None:
    client, _ = client_with_key
    r = client.post("/api/tasks/gc")
    assert r.status_code == 401


def test_task_sync_rejected_without_bearer(client_with_key) -> None:
    client, _ = client_with_key
    r = client.post("/api/tasks/sync")
    assert r.status_code == 401


def test_task_list_allowed_without_bearer(client_with_key) -> None:
    """GET /api/tasks should remain public (read endpoints aren't bearer-gated)."""
    client, _ = client_with_key
    r = client.get("/api/tasks")
    # May return 500 if Supabase isn't configured in tests, but NOT 401
    assert r.status_code != 401
