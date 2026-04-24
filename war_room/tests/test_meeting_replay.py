"""Tests for /api/meeting/{id}/replay."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path))
    import war_room.dashboard.meeting_log as ml

    importlib.reload(ml)
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    return TestClient(srv.app), ml


@pytest.mark.asyncio
async def test_replay_returns_frames_with_timing(client) -> None:
    c, ml = client
    # Seed a few meeting_* events. ts present on some, absent on others.
    for i in range(3):
        await ml.meeting_log.record({"type": "meeting_message", "meeting_id": "m1", "ts": 1700000000 + i})
    r = c.get("/api/meeting/m1/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["meeting_id"] == "m1"
    assert body["frame_count"] == 3
    assert len(body["frames"]) == 3
    assert body["frames"][0]["delay_ms"] >= 0
    # First frame uses the default (no prev) — should be the default gap or 0
    assert body["frames"][1]["delay_ms"] == 1000  # 1s gap at speed 1.0


@pytest.mark.asyncio
async def test_replay_speed_shortens_gaps(client) -> None:
    c, ml = client
    await ml.meeting_log.record({"type": "meeting_message", "meeting_id": "m2", "ts": 1700000000})
    await ml.meeting_log.record({"type": "meeting_message", "meeting_id": "m2", "ts": 1700000001})
    r_slow = c.get("/api/meeting/m2/replay", params={"speed": 1.0})
    r_fast = c.get("/api/meeting/m2/replay", params={"speed": 5.0})
    slow = r_slow.json()["frames"][1]["delay_ms"]
    fast = r_fast.json()["frames"][1]["delay_ms"]
    assert fast < slow
    assert slow == 1000
    assert fast == 200


def test_replay_unknown_meeting_empty(client) -> None:
    c, _ = client
    r = c.get("/api/meeting/nonexistent/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["frame_count"] == 0
    assert body["frames"] == []


@pytest.mark.asyncio
async def test_replay_cap_honoured(client) -> None:
    c, ml = client
    for _ in range(50):
        await ml.meeting_log.record({"type": "meeting_message", "meeting_id": "m3"})
    r = c.get("/api/meeting/m3/replay", params={"cap": 10})
    assert r.json()["frame_count"] == 10
