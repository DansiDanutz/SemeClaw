"""End-to-end HTTP smoke tests for the v1 surface.

Builds a minimal FastAPI app, mounts ``register_v1`` onto it, and walks
the public endpoints with the synchronous TestClient. This catches
wiring bugs (missing handler, wrong status code, broken JSON contract)
that the per-module unit tests can't see.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEMECLAW_ADMIN_KEY", "admin-test-key")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

    # Reset registration flag + in-process billing queue across tests.
    from war_room.v1 import billing, routes

    billing._PENDING.clear()
    billing._LAST_FLUSH = 0.0

    app = FastAPI()
    routes.register_v1(app)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /api/v1/about
# ---------------------------------------------------------------------------
def test_about_endpoint_lists_features(client):
    r = client.get("/api/v1/about")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]  # any non-empty version string is fine
    # Spot check core features.
    for feat in ("tenants_crud", "audit_log", "dlq_replay", "discord_adapter"):
        assert feat in body["features"]
    # Endpoint inventory must include the tenant + audit + dialog surfaces.
    assert "tenants" in body["endpoints"]
    assert "POST /api/tenants" in body["endpoints"]["tenants"]


# ---------------------------------------------------------------------------
# Tenants CRUD
# ---------------------------------------------------------------------------
def test_tenants_crud_round_trip(client):
    # Create
    r = client.post("/api/tenants", json={"name": "Acme", "plan": "pro"})
    assert r.status_code == 201
    body = r.json()
    assert body["plan"] == "pro"
    assert body["api_key"].startswith("sck_"), "plain api key must be returned once"
    tid = body["id"]

    # List
    r = client.get("/api/tenants")
    assert r.status_code == 200
    assert any(t["id"] == tid for t in r.json()["tenants"])

    # Get
    r = client.get(f"/api/tenants/{tid}")
    assert r.status_code == 200
    assert r.json()["id"] == tid
    assert "api_key" not in r.json(), "plain key must not leak on read"

    # Update plan
    r = client.patch(f"/api/tenants/{tid}/plan", json={"plan": "enterprise"})
    assert r.status_code == 200
    assert r.json()["plan"] == "enterprise"

    # Delete
    r = client.delete(f"/api/tenants/{tid}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


def test_tenants_create_rejects_missing_name(client):
    r = client.post("/api/tenants", json={"plan": "free"})
    assert r.status_code == 400


def test_tenants_update_plan_rejects_unknown(client):
    r = client.post("/api/tenants", json={"name": "Acme"})
    tid = r.json()["id"]
    r = client.patch(f"/api/tenants/{tid}/plan", json={"plan": "diamond"})
    assert r.status_code == 400


def test_tenants_get_404_for_unknown(client):
    r = client.get("/api/tenants/tnt_unknown")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------
def test_billing_status_reports_unconfigured(client):
    r = client.get("/api/v1/billing/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert "STRIPE_SECRET_KEY" in body["required_env"]


def test_billing_customer_requires_tenant_id(client):
    r = client.post("/api/v1/billing/customer", json={})
    assert r.status_code == 400


def test_billing_customer_404_for_unknown_tenant(client):
    r = client.post("/api/v1/billing/customer", json={"tenant_id": "tnt_unknown"})
    assert r.status_code == 404


def test_billing_webhook_rejects_invalid_signature(client):
    r = client.post(
        "/api/v1/billing/webhook",
        content=b'{"id":"evt_1","type":"customer.subscription.deleted"}',
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert r.status_code == 400


def test_billing_flush_drains_queue(client):
    r = client.post("/api/v1/billing/flush")
    assert r.status_code == 200
    body = r.json()
    # No records queued, flushed == 0.
    assert body["flushed"] == 0


# ---------------------------------------------------------------------------
# Dialog preview
# ---------------------------------------------------------------------------
def test_dialog_preview_returns_structured_payload(client):
    r = client.post(
        "/api/v1/dialog/preview",
        json={"title": "demo", "comments": ["lgtm", "agreed"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "task" in body and "lines" in body
    assert isinstance(body["convergence"], list)
    assert len(body["convergence"]) == 2


# ---------------------------------------------------------------------------
# Adapters probe
# ---------------------------------------------------------------------------
def test_adapters_probe_lists_all(client, monkeypatch):
    for env in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CHANNEL_ID",
        "LINEAR_API_KEY",
        "NOTION_TOKEN",
        "NOTION_DATABASE_ID",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(env, raising=False)
    r = client.get("/api/v1/adapters")
    assert r.status_code == 200
    ids = sorted(p["id"] for p in r.json()["adapters"])
    assert ids == ["discord", "jira", "linear", "notion"]


# ---------------------------------------------------------------------------
# Admin SPA static guard
# ---------------------------------------------------------------------------
def test_admin_static_blocks_path_traversal(client):
    # Both encoded and direct traversal attempts must 404, not 200.
    r = client.get("/admin/static/../../../../etc/passwd")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Admin gating
# ---------------------------------------------------------------------------
def test_admin_audit_query_works_without_filters(client):
    r = client.get("/api/admin/audit")
    assert r.status_code == 200
    body = r.json()
    assert "entries" in body
    assert "filters" in body


# ---------------------------------------------------------------------------
# Health aggregator
# ---------------------------------------------------------------------------
def test_health_endpoint_aggregates_subsystems(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data_dir_writable"] is True
    assert body["billing_configured"] is False
    # Adapter list reflects the same set as /api/v1/adapters.
    adapter_ids = sorted(a["id"] for a in body["adapters"])
    assert adapter_ids == ["discord", "jira", "linear", "notion"]
    # DLQ depth keys are present even when empty.
    assert isinstance(body["dlq_depth"], dict)


def test_about_lists_billing_endpoints(client):
    r = client.get("/api/v1/about")
    assert r.status_code == 200
    body = r.json()
    assert "billing" in body["endpoints"]
    assert "GET /api/v1/billing/status" in body["endpoints"]["billing"]
    assert "stripe_billing" in body["features"]
    assert "billing_configured" in body
