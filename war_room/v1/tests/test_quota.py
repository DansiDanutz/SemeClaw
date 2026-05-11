"""Tests for the plan-quota enforcement middleware.

We mount the middleware on a throwaway FastAPI app and drive it with the
TestClient. No external services touched.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


def _make_app() -> FastAPI:
    from war_room.v1 import quota

    app = FastAPI()
    quota.install(app)

    @app.post("/api/tasks/{tid}/intervene")
    async def _intervene(tid: str):
        return {"ok": True, "task_id": tid}

    @app.post("/api/v1/dialog/preview")
    async def _preview():
        return {"ok": True}

    @app.post("/api/other")
    async def _other():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_unscoped_request_passes_through(v1_data_dir):
    app = _make_app()
    with TestClient(app) as c:
        r = c.post("/api/tasks/abc/intervene", json={})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_free_plan_blocks_after_cap(v1_data_dir):
    from war_room.v1 import tenants, usage

    tenant, key = await tenants.create_tenant("Tiny", plan="free")
    # Push usage to the cap.
    await usage.increment(tenant.id, meetings=10)

    app = _make_app()
    with TestClient(app) as c:
        r = c.post("/api/tasks/x/intervene", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 402
        body = r.json()
        assert body["error"] == "plan_quota_exceeded"
        assert body["used"] == 10
        assert body["limit"] == 10
        assert "X-SemeClaw-Quota" in r.headers


@pytest.mark.asyncio
async def test_pro_plan_increments_on_success(v1_data_dir):
    from war_room.v1 import tenants, usage

    tenant, key = await tenants.create_tenant("Big", plan="pro")

    app = _make_app()
    with TestClient(app) as c:
        r = c.post("/api/v1/dialog/preview", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200

    snap = await usage.snapshot(tenant.id)
    assert snap["meetings"] == 1


@pytest.mark.asyncio
async def test_unmetered_route_is_not_counted(v1_data_dir):
    from war_room.v1 import tenants, usage

    tenant, key = await tenants.create_tenant("Big", plan="pro")
    app = _make_app()
    with TestClient(app) as c:
        r = c.post("/api/other", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200
    snap = await usage.snapshot(tenant.id)
    assert snap["meetings"] == 0


@pytest.mark.asyncio
async def test_suspended_tenant_blocked_with_402(v1_data_dir):
    from war_room.v1 import tenants

    tenant, key = await tenants.create_tenant("Paused", plan="pro")
    data = await tenants._read_all()
    data[tenant.id]["status"] = "suspended"
    await tenants._write_all(data)

    app = _make_app()
    with TestClient(app) as c:
        r = c.post("/api/tasks/x/intervene", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 402
        assert r.json()["error"] == "tenant_inactive"
