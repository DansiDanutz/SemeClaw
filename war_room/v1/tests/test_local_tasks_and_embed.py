"""Tests for the local task store + embed-v1 SDK."""

from __future__ import annotations

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEMECLAW_LOCAL_TASKS", "1")
    yield tmp_path


@pytest.mark.asyncio
async def test_upsert_dedups_on_source_tuple(v1_data_dir):
    from war_room.v1 import local_tasks as lt

    a = await lt.upsert_task(
        source="discord", source_id="msg-1", tenant_id="acme", title="A",
        description="", status="open", assigned_agents=[],
    )
    b = await lt.upsert_task(
        source="discord", source_id="msg-1", tenant_id="acme", title="A2",
        description="", status="open", assigned_agents=["research"],
    )
    assert a == b
    task = await lt.get_task(a)
    assert task["title"] == "A2"
    assert task["assigned_agents"] == ["research"]


@pytest.mark.asyncio
async def test_dialog_and_intervention_round_trip(v1_data_dir):
    from war_room.v1 import local_tasks as lt

    tid = await lt.upsert_task(
        source="local", source_id="x", tenant_id="acme", title="Probe",
        description="d", status="open", assigned_agents=["research"],
    )
    d1 = await lt.insert_dialog(tid, version=1, lines=[{"agent_id": "host", "text": "hi"}])
    iv = await lt.insert_intervention(
        task_id=tid, dialog_id=d1["id"], turn_index=1,
        user_comment="focus EU", agent_replies=[{"agent_id": "research", "text": "noted"}],
    )
    rows = await lt.list_interventions(d1["id"])
    assert [r["id"] for r in rows] == [iv["id"]]

    d2 = await lt.insert_dialog(tid, version=2, lines=[])
    await lt.supersede_dialog(d1["id"], d2["id"])
    updated = await lt.get_dialog(d1["id"])
    assert updated["superseded_by"] == d2["id"]
    latest = await lt.latest_dialog(tid)
    assert latest["id"] == d2["id"]


@pytest.mark.asyncio
async def test_seed_demo_task_is_idempotent(v1_data_dir):
    from war_room.v1 import local_tasks as lt

    a = await lt.seed_demo_task()
    b = await lt.seed_demo_task()
    assert a["id"] == b["id"]
    assert a["meta"].get("seeded") is True


def test_embed_v1_sdk_contains_required_methods():
    from war_room.v1 import routes

    assert "window.SemeClaw" in routes.EMBED_V1_JS
    for fn in ("adNext", "renderAd", "spotlight", "renderSpotlight"):
        assert fn in routes.EMBED_V1_JS
    # Auto-mount selectors are documented in the SDK.
    assert "data-semeclaw-ad" in routes.EMBED_V1_JS
    assert "data-semeclaw-spotlight" in routes.EMBED_V1_JS
