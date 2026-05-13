"""Tests for Stripe billing wire-up.

Network calls are exercised in a stripe-not-configured mode that
short-circuits to deterministic DLQ persistence — no live calls.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


@pytest.mark.asyncio
async def test_ensure_customer_returns_none_when_not_configured(monkeypatch, v1_data_dir):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from war_room.v1 import billing, tenants

    tenant, _ = await tenants.create_tenant("Acme", plan="pro")
    cid = await billing.ensure_customer(tenant)
    assert cid is None


@pytest.mark.asyncio
async def test_report_usage_lands_in_dlq_when_unconfigured(monkeypatch, v1_data_dir, tmp_path):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from war_room.v1 import billing

    # Reset module state between tests — billing keeps in-memory queue timers.
    billing._PENDING.clear()
    billing._LAST_FLUSH = billing.time.time()

    await billing.report_usage("acme", "meetings", quantity=2)
    await billing.report_usage("acme", "tts_chars", quantity=3500)
    result = await billing.flush()
    assert result["flushed"] == 0
    assert result["dlq"] == 2

    dlq = tmp_path / "billing_usage_dlq.jsonl"
    assert dlq.exists()
    lines = [json.loads(line) for line in dlq.read_text().splitlines() if line.strip()]
    assert {l["usage_kind"] for l in lines} == {"meetings", "tts_chars"}
    assert all(l["kind"] == "billing_usage" for l in lines)


def test_verify_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    from war_room.v1 import billing

    assert billing.verify_webhook(b"{}", "t=1,v1=bogus") is None
    # Missing parts.
    assert billing.verify_webhook(b"{}", "v1=anything") is None


def test_verify_webhook_accepts_valid_signature(monkeypatch):
    import hashlib
    import hmac
    import time

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    from war_room.v1 import billing

    payload = json.dumps(
        {"id": "evt_1", "type": "invoice.payment_failed", "data": {"object": {"customer": "cus_x"}}}
    ).encode()
    ts = str(int(time.time()))
    signed = f"{ts}.{payload.decode()}".encode()
    sig = hmac.new(b"whsec_test", signed, hashlib.sha256).hexdigest()
    event = billing.verify_webhook(payload, f"t={ts},v1={sig}")
    assert event is not None
    assert event.type == "invoice.payment_failed"


@pytest.mark.asyncio
async def test_apply_webhook_suspends_on_payment_failure(monkeypatch, v1_data_dir):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    from war_room.v1 import billing, tenants

    tenant, _ = await tenants.create_tenant("PayCo", plan="pro")
    # Patch the stripe_customer_id directly through the storage layer.
    data = await tenants._read_all()
    data[tenant.id]["stripe_customer_id"] = "cus_paid"
    await tenants._write_all(data)

    event = billing.WebhookEvent(id="evt_1", type="invoice.payment_failed", data={"object": {"customer": "cus_paid"}})
    out = await billing.apply_webhook(event)
    assert out["applied"] is True

    updated = await tenants.get_tenant(tenant.id)
    assert updated is not None
    assert updated.status == "suspended"
