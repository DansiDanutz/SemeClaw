"""Tests for the plan-based quota middleware.

The middleware intercepts metered routes, resolves the tenant from the
bearer token or X-Tenant-Id header, and returns 402 when the tenant is
inactive or over its plan cap. These tests build a tiny FastAPI app
with one metered route and walk the contract end-to-end.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    from war_room.v1 import audit, quota

    app = FastAPI()
    audit.install(app)
    quota.install(app)

    @app.post("/api/v1/dialog/preview")
    async def preview(payload: dict | None = None):  # noqa: ARG001
        return JSONResponse({"ok": True})

    @app.post("/api/meeting/finalize")
    async def finalize(payload: dict | None = None):  # noqa: ARG001
        return JSONResponse({"finalized": True})

    @app.post("/api/unmetered")
    async def unmetered(payload: dict | None = None):  # noqa: ARG001
        return JSONResponse({"ok": True})

    return app


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.mark.asyncio
async def test_quota_unmetered_route_passes_through(client):
    r = client.post("/api/unmetered", json={})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_quota_without_tenant_passes_through(client):
    # No bearer token, no X-Tenant-Id — middleware lets it through.
    r = client.post("/api/v1/dialog/preview", json={})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_quota_blocks_when_tenant_inactive(client):
    from war_room.v1 import tenants

    tenant, plain_key = await tenants.create_tenant("Suspended", plan="pro")
    # Suspend manually via storage layer (mimics a payment_failed webhook).
    data = await tenants._read_all()
    data[tenant.id]["status"] = "suspended"
    await tenants._write_all(data)

    r = client.post(
        "/api/v1/dialog/preview",
        json={},
        headers={"authorization": f"Bearer {plain_key}"},
    )
    assert r.status_code == 402
    body = r.json()
    assert body["error"] == "tenant_inactive"
    assert body["tenant_id"] == tenant.id


@pytest.mark.asyncio
async def test_quota_blocks_when_over_plan_cap(client):
    from war_room.v1 import tenants, usage

    tenant, plain_key = await tenants.create_tenant("Tiny", plan="free")
    # Free plan has 10 meetings/mo — push past it.
    await usage.increment(tenant.id, meetings=11)

    r = client.post(
        "/api/meeting/finalize",
        json={},
        headers={"authorization": f"Bearer {plain_key}"},
    )
    assert r.status_code == 402
    body = r.json()
    assert body["error"] == "plan_quota_exceeded"
    assert body["plan"] == "free"
    assert body["used"] >= 11
    # Header tells operators why the gate fired without inspecting the body.
    assert "X-SemeClaw-Quota" in r.headers
    assert "Retry-After" in r.headers


@pytest.mark.asyncio
async def test_quota_increments_usage_on_success(client):
    from war_room.v1 import tenants, usage

    tenant, plain_key = await tenants.create_tenant("Counter", plan="pro")

    before = await usage.snapshot(tenant.id)
    assert before.get("meetings", 0) == 0

    r = client.post(
        "/api/meeting/finalize",
        json={},
        headers={"authorization": f"Bearer {plain_key}"},
    )
    assert r.status_code == 200

    after = await usage.snapshot(tenant.id)
    assert after["meetings"] == 1


@pytest.mark.asyncio
async def test_quota_attributes_via_x_tenant_id_with_admin_key(client, monkeypatch):
    """When the global admin key is used, X-Tenant-Id attributes the
    metered call to a specific tenant for usage tracking."""
    from war_room.v1 import tenants, usage

    monkeypatch.setenv("SEMECLAW_API_KEY", "admin-key")
    tenant, _ = await tenants.create_tenant("Attributed", plan="pro")

    r = client.post(
        "/api/v1/dialog/preview",
        json={},
        headers={
            "authorization": "Bearer admin-key",
            "x-tenant-id": tenant.id,
        },
    )
    assert r.status_code == 200
    after = await usage.snapshot(tenant.id)
    assert after.get("meetings", 0) == 1


# ---------------------------------------------------------------------------
# Stripe webhook — full HTTP wire-up
# ---------------------------------------------------------------------------
@pytest.fixture()
def webhook_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_smoke")

    from war_room.v1 import billing, routes

    billing._PENDING.clear()
    app = FastAPI()
    routes.register_v1(app)
    return app


def test_webhook_endpoint_round_trip_with_valid_signature(webhook_app):
    from war_room.v1 import billing, tenants
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        tenant, _ = loop.run_until_complete(tenants.create_tenant("Stripey", plan="free"))
        # Pin a stripe_customer_id so the webhook can find the tenant.
        data = loop.run_until_complete(tenants._read_all())
        data[tenant.id]["stripe_customer_id"] = "cus_smoke"
        loop.run_until_complete(tenants._write_all(data))
    finally:
        loop.close()

    payload = json.dumps(
        {
            "id": "evt_smoke",
            "type": "customer.subscription.updated",
            "data": {"object": {"customer": "cus_smoke", "metadata": {"plan": "enterprise"}}},
        }
    ).encode()
    ts = int(time.time())
    signed = f"{ts}.{payload.decode()}".encode()
    sig = hmac.new(b"whsec_smoke", signed, hashlib.sha256).hexdigest()

    with TestClient(webhook_app) as c:
        r = c.post(
            "/api/v1/billing/webhook",
            content=payload,
            headers={"stripe-signature": f"t={ts},v1={sig}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["applied"] is True
    assert body["plan"] == "enterprise"
