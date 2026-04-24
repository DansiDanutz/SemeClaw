"""Tests for impression recording: retry + dead-letter queue on exhaustion."""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Import adclaw.server with DLQ pointed at a temp path, no real Supabase."""
    dlq = tmp_path / "dlq.jsonl"
    monkeypatch.setenv("ADCLAW_DLQ_PATH", str(dlq))
    monkeypatch.setenv("ADCLAW_IMPRESSION_RETRIES", "2")  # fast tests
    monkeypatch.setenv("DLS_TEAM_SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("DLS_TEAM_SUPABASE_SERVICE_KEY", "stub")
    import adclaw.server as mod

    importlib.reload(mod)
    yield mod, dlq


@pytest.mark.asyncio
async def test_retry_then_success(server, monkeypatch) -> None:
    mod, _dlq = server
    calls = {"n": 0}

    async def flaky(method, path, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("supa 503")
        return []

    monkeypatch.setattr(mod, "_supa", flaky)
    # Should succeed on 2nd attempt, no raise
    await mod._supa_with_retry("post", "anything", json={})
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_dlq_write_on_exhaustion(server, monkeypatch) -> None:
    mod, dlq = server

    async def always_fail(method, path, **kw):
        raise RuntimeError("supa down")

    monkeypatch.setattr(mod, "_supa", always_fail)

    await mod._record_impression(
        slide_id="s1",
        campaign_id="c1",
        instance_id="inst1",
        ip_hash="abc",
        tenant_id="t",
        now_iso="2026-04-24T00:00:00Z",
    )
    # DLQ should contain at least the impression + deduct lines
    lines = [l for l in dlq.read_text().splitlines() if l.strip()]
    kinds = [json.loads(l)["kind"] for l in lines]
    assert "impression" in kinds, f"expected impression in DLQ kinds, got {kinds}"
    assert "deduct" in kinds, f"expected deduct in DLQ kinds, got {kinds}"


@pytest.mark.asyncio
async def test_record_does_not_raise_when_supa_down(server, monkeypatch) -> None:
    """The serve path must never crash because of impression-write failures."""
    mod, _dlq = server

    async def always_fail(method, path, **kw):
        raise ConnectionError("network")

    monkeypatch.setattr(mod, "_supa", always_fail)

    # Must return normally, no exception
    await mod._record_impression(
        slide_id="s",
        campaign_id="c",
        instance_id="i",
        ip_hash="",
        tenant_id="t",
        now_iso="2026-04-24T00:00:00Z",
    )
