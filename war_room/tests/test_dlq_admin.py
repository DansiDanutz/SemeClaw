"""Tests for the /api/admin/dlq endpoints."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

KEY = "test-admin-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMECLAW_API_KEY", KEY)
    adlq = tmp_path / "adclaw.jsonl"
    tdlq = tmp_path / "tasks.jsonl"
    adlq.write_text('{"kind":"impression","n":1}\n{"kind":"deduct","n":2}\n')
    monkeypatch.setenv("ADCLAW_DLQ_PATH", str(adlq))
    monkeypatch.setenv("SEMECLAW_TASKS_DLQ_PATH", str(tdlq))
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    return TestClient(srv.app), adlq, tdlq


def test_dlq_list_shows_registered_queues(client) -> None:
    c, adlq, _ = client
    r = c.get("/api/admin/dlq", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200
    body = r.json()
    assert "adclaw" in body["dlqs"]
    assert body["dlqs"]["adclaw"]["exists"] is True
    assert body["dlqs"]["adclaw"]["lines"] == 2


def test_dlq_peek_head(client) -> None:
    c, _, _ = client
    r = c.get("/api/admin/dlq/adclaw?head=1", headers={"Authorization": f"Bearer {KEY}"})
    body = r.json()
    assert body["total"] == 2
    assert len(body["head"]) == 1
    assert body["head"][0]["kind"] == "impression"


def test_dlq_drain_archives_file(client) -> None:
    c, adlq, _ = client
    r = c.post("/api/admin/dlq/adclaw/drain", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200
    body = r.json()
    assert body["archived_to"].startswith(str(adlq))
    assert not adlq.exists()


def test_dlq_requires_bearer(client) -> None:
    c, _, _ = client
    r = c.get("/api/admin/dlq")
    assert r.status_code == 401


def test_dlq_unknown_name_404(client) -> None:
    c, _, _ = client
    r = c.get("/api/admin/dlq/nope", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 404


def test_dlq_requires_api_key_set() -> None:
    """With no SEMECLAW_API_KEY set, admin endpoints return 503."""
    import os

    if "SEMECLAW_API_KEY" in os.environ:
        del os.environ["SEMECLAW_API_KEY"]
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    c = TestClient(srv.app)
    r = c.get("/api/admin/dlq")
    assert r.status_code == 503
