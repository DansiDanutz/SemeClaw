"""Tests for the shared supa-durable helper + tasks DLQ wiring."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from war_room.utils.supa_durable import dlq_append, with_retry


@pytest.mark.asyncio
async def test_with_retry_success_after_flake() -> None:
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    got = await with_retry(flaky, attempts=3)
    assert got == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retry_raises_after_exhaustion() -> None:
    async def never():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await with_retry(never, attempts=2)


def test_dlq_append_creates_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "dlq.jsonl"
    dlq_append(p, {"kind": "x", "n": 1})
    dlq_append(p, {"kind": "x", "n": 2})
    lines = [json.loads(l) for l in p.read_text().splitlines()]
    assert [e["n"] for e in lines] == [1, 2]


def test_dlq_append_never_raises_on_bad_path(tmp_path: Path) -> None:
    # Point at a path whose parent cannot be created — the helper must swallow.
    bogus = tmp_path / "file-as-dir" / "nested" / "dlq.jsonl"
    (tmp_path / "file-as-dir").write_text("I am a file, not a dir")
    # Must return cleanly, not raise
    dlq_append(bogus, {"kind": "x"})


@pytest.mark.asyncio
async def test_tasks_supa_writes_dlq_on_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEMECLAW_TASKS_DLQ_PATH", str(tmp_path / "tasks_dlq.jsonl"))
    monkeypatch.setenv("SEMECLAW_SUPA_RETRIES", "2")
    monkeypatch.setenv("DLS_TEAM_SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("DLS_TEAM_SUPABASE_SERVICE_KEY", "stub")
    from war_room.tasks import _db

    importlib.reload(_db)

    async def always_fail(*a, **kw):
        raise _db.TasksDBError("boom")

    monkeypatch.setattr(_db, "_supa_once", always_fail)

    with pytest.raises(_db.TasksDBError):
        await _db.supa("post", "semeclaw_tasks", json={"id": "t1"})

    dlq = tmp_path / "tasks_dlq.jsonl"
    assert dlq.exists(), "DLQ file should have been created"
    entry = json.loads(dlq.read_text().splitlines()[0])
    assert entry["kind"] == "tasks_post"
    assert entry["path"] == "semeclaw_tasks"
    assert entry["payload"] == {"id": "t1"}
