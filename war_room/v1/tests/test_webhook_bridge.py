"""Tests that the legacy ``_dispatch_webhook`` in the dashboard server
fans out to the v1 tenant-scoped webhook system.

We don't boot the FastAPI app — we import the dispatcher directly and
verify the v1 bridge is exercised. Real HTTP delivery is intercepted at
the v1 ``dispatch`` layer by asserting it queued a task.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


@pytest.mark.asyncio
async def test_legacy_dispatch_bridges_to_v1_webhooks(v1_data_dir, monkeypatch):
    """Subscribing through the v1 surface and then triggering a legacy
    server-level event must reach the v1 subscription."""
    from war_room.dashboard import server
    from war_room.v1 import webhooks as v1_hooks

    await v1_hooks.subscribe(
        tenant_id="default",
        url="https://8.8.8.8/listener",
        events=["meeting.finalized", "intervention.recorded"],
    )

    captured: list[tuple[str, dict, str | None]] = []

    async def fake_dispatch(event, payload, *, tenant_id=None):
        captured.append((event, payload, tenant_id))
        return 1

    monkeypatch.setattr(v1_hooks, "dispatch", fake_dispatch)

    await server._dispatch_webhook("meeting.finalized", {"meeting_id": "m-1"})
    # asyncio.create_task is fire-and-forget — let it drain.
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    event, payload, tenant_id = captured[0]
    assert event == "meeting.finalized"
    # The bridge passes the FULL envelope (event/ts/agent_version/tenant_id/data),
    # not just the raw payload — that way subscribers see what /api/events sees.
    assert "data" in payload
    assert payload["data"] == {"meeting_id": "m-1"}
    assert payload["event"] == "meeting.finalized"
    # Tenant routing falls back to SEMECLAW_TENANT_ID when payload has none.
    assert tenant_id


@pytest.mark.asyncio
async def test_legacy_dispatch_routes_explicit_tenant_id(v1_data_dir, monkeypatch):
    from war_room.dashboard import server
    from war_room.v1 import webhooks as v1_hooks

    captured: list[str | None] = []

    async def fake_dispatch(event, payload, *, tenant_id=None):  # noqa: ARG001
        captured.append(tenant_id)
        return 0

    monkeypatch.setattr(v1_hooks, "dispatch", fake_dispatch)

    await server._dispatch_webhook("report.created", {"tenant_id": "acme-tenant"})
    await asyncio.sleep(0.05)

    assert captured == ["acme-tenant"]


@pytest.mark.asyncio
async def test_legacy_dispatch_continues_when_v1_bridge_raises(v1_data_dir, monkeypatch):
    """A broken v1 install must NOT break the legacy SSE/webhook path —
    the bridge swallows and logs, never propagates."""
    from war_room.dashboard import server
    from war_room.v1 import webhooks as v1_hooks

    async def boom(event, payload, *, tenant_id=None):  # noqa: ARG001
        raise RuntimeError("simulated v1 failure")

    monkeypatch.setattr(v1_hooks, "dispatch", boom)

    # Should not raise — the legacy dispatcher swallows v1 errors.
    await server._dispatch_webhook("report.deleted", {"name": "x.md"})
    await asyncio.sleep(0.05)
