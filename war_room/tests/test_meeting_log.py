"""Tests for durable meeting transcript log + backfill."""

from __future__ import annotations

from pathlib import Path

import pytest

from war_room.dashboard.meeting_log import MeetingLog


@pytest.mark.asyncio
async def test_records_seq_monotonic(tmp_path: Path) -> None:
    log = MeetingLog(dir_path=tmp_path)
    m1 = {"type": "meeting_message", "meeting_id": "m1", "text": "hello"}
    m2 = {"type": "meeting_task_chunk", "meeting_id": "m1", "chunk": "hi "}
    m3 = {"type": "meeting_task_chunk", "meeting_id": "m1", "chunk": "there"}
    await log.record(m1)
    await log.record(m2)
    await log.record(m3)
    assert [m["seq"] for m in (m1, m2, m3)] == [1, 2, 3]


@pytest.mark.asyncio
async def test_ignores_non_persistable(tmp_path: Path) -> None:
    log = MeetingLog(dir_path=tmp_path)
    msg = {"type": "new_log"}  # not a meeting_* type
    await log.record(msg)
    assert "seq" not in msg
    # No file should have been created
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_backfill_filters_by_since(tmp_path: Path) -> None:
    log = MeetingLog(dir_path=tmp_path)
    for i in range(5):
        await log.record({"type": "meeting_message", "meeting_id": "m1", "n": i})
    got = await log.backfill("m1", since=2)
    assert [e["seq"] for e in got] == [3, 4, 5]


@pytest.mark.asyncio
async def test_resume_across_instances(tmp_path: Path) -> None:
    """Restart simulation: second instance continues numbering."""
    a = MeetingLog(dir_path=tmp_path)
    await a.record({"type": "meeting_message", "meeting_id": "m1"})
    await a.record({"type": "meeting_message", "meeting_id": "m1"})
    b = MeetingLog(dir_path=tmp_path)  # new instance, same dir
    out: dict = {"type": "meeting_message", "meeting_id": "m1"}
    await b.record(out)
    assert out["seq"] == 3  # continues, doesn't reset


@pytest.mark.asyncio
async def test_meeting_id_sanitization(tmp_path: Path) -> None:
    """Path-traversal-ish meeting ids are scrubbed, not written verbatim."""
    log = MeetingLog(dir_path=tmp_path)
    m = {"type": "meeting_message", "meeting_id": "../../etc/passwd"}
    await log.record(m)
    # Nothing should be written above tmp_path
    written = list(tmp_path.iterdir())
    assert len(written) == 1
    # Filename should contain no slashes
    assert "/" not in written[0].name
    assert ".." not in written[0].name


@pytest.mark.asyncio
async def test_missing_meeting_returns_empty(tmp_path: Path) -> None:
    log = MeetingLog(dir_path=tmp_path)
    assert await log.backfill("nonexistent") == []


@pytest.mark.asyncio
async def test_backfill_limit(tmp_path: Path) -> None:
    log = MeetingLog(dir_path=tmp_path)
    for _ in range(20):
        await log.record({"type": "meeting_message", "meeting_id": "m1"})
    got = await log.backfill("m1", since=0, limit=5)
    assert len(got) == 5
