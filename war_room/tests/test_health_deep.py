"""Tests for /api/health/deep."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADCLAW_DLQ_PATH", str(tmp_path / "adclaw_dlq.jsonl"))
    monkeypatch.setenv("SEMECLAW_TASKS_DLQ_PATH", str(tmp_path / "tasks_dlq.jsonl"))
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    return TestClient(srv.app)


def test_deep_health_shape(client) -> None:
    r = client.get("/api/health/deep")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "status" in body and body["status"] in ("ok", "degraded", "down")
    assert "ok" in body
    assert "checks" in body
    for k in ("supabase", "stripe", "tts", "dlq", "disk", "version"):
        assert k in body["checks"], f"missing subsystem: {k}"
        check = body["checks"][k]
        assert "status" in check and "ok" in check


def test_deep_health_degrades_when_supabase_unset(client, monkeypatch) -> None:
    # Default in test env: no Supabase vars set -> unconfigured -> overall degraded
    r = client.get("/api/health/deep")
    body = r.json()
    assert body["checks"]["supabase"]["status"] in ("unconfigured", "down")
    assert body["status"] in ("degraded", "down")


def test_deep_health_dlq_growth_reported(client, tmp_path) -> None:
    dlq = tmp_path / "adclaw_dlq.jsonl"
    dlq.write_text('{"kind":"x"}\n' * 1000)  # small file
    r = client.get("/api/health/deep")
    body = r.json()
    assert body["checks"]["dlq"]["total_bytes"] > 0
