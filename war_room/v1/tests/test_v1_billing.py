"""Tests for Stripe metered billing + HTTP routes smoke.

Every test runs with ``STRIPE_SECRET_KEY`` deliberately unset unless the
test sets it explicitly. Network calls are never made — the helpers all
no-op or DLQ when Stripe is not configured, which is the contract we
want to verify here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def billing_module(v1_data_dir, monkeypatch):
    """A fresh billing module with pending queue cleared each test."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    from war_room.v1 import billing

    billing._PENDING.clear()
    billing._LAST_FLUSH = 0.0
    return billing


# ---------------------------------------------------------------------------
# Configuration sentinel
# ---------------------------------------------------------------------------
def test_is_configured_false_without_secret(billing_module):
    assert billing_module.is_configured() is False


def test_is_configured_true_with_secret(billing_module, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    assert billing_module.is_configured() is True


# ---------------------------------------------------------------------------
# ensure_customer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_customer_noop_when_unconfigured(billing_module):
    from war_room.v1 import tenants

    tenant, _ = await tenants.create_tenant("Acme", plan="free")
    cid = await billing_module.ensure_customer(tenant)
    assert cid is None


# ---------------------------------------------------------------------------
# report_usage + flush
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_report_usage_rejects_non_positive(billing_module):
    await billing_module.report_usage("acme", "meetings", quantity=0)
    await billing_module.report_usage("acme", "meetings", quantity=-3)
    assert billing_module.pending_count() == 0


@pytest.mark.asyncio
async def test_report_usage_queues_record(billing_module):
    # Force the in-process clock past the flush window so report_usage
    # doesn't drain it before we assert the queue depth.
    billing_module._LAST_FLUSH = time.time()
    await billing_module.report_usage("acme", "meetings", quantity=2)
    assert billing_module.pending_count() == 1
    rec = billing_module._PENDING[0]
    assert rec.tenant_id == "acme"
    assert rec.kind == "meetings"
    assert rec.quantity == 2


@pytest.mark.asyncio
async def test_flush_unconfigured_routes_to_dlq(billing_module, v1_data_dir):
    billing_module._LAST_FLUSH = time.time()
    await billing_module.report_usage("acme", "meetings", quantity=1)
    await billing_module.report_usage("acme", "tts_chars", quantity=500)

    result = await billing_module.flush()
    assert result["flushed"] == 0
    assert result["dlq"] == 2
    # Pending queue is drained even on DLQ-only flushes.
    assert billing_module.pending_count() == 0

    dlq_file = v1_data_dir / "billing_usage_dlq.jsonl"
    assert dlq_file.exists()
    rows = [json.loads(line) for line in dlq_file.read_text().splitlines() if line.strip()]
    kinds = sorted(r["usage_kind"] for r in rows)
    assert kinds == ["meetings", "tts_chars"]
    # Every DLQ row records a reason so operators can debug.
    assert all(r.get("reason") for r in rows)


@pytest.mark.asyncio
async def test_flush_empty_queue_is_clean(billing_module):
    result = await billing_module.flush()
    assert result == {"flushed": 0, "queued": 0, "dlq": 0}


# ---------------------------------------------------------------------------
# verify_webhook
# ---------------------------------------------------------------------------
def _sign(secret: str, payload: bytes, ts: int | None = None) -> tuple[str, int]:
    """Build a valid Stripe-Signature header value for a given secret."""
    ts = ts if ts is not None else int(time.time())
    signed = f"{ts}.{payload.decode('utf-8', 'replace')}".encode()
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}", ts


def test_verify_webhook_requires_secret(billing_module):
    payload = b'{"id":"evt_1","type":"customer.subscription.deleted"}'
    assert billing_module.verify_webhook(payload, "t=1,v1=deadbeef") is None


def test_verify_webhook_rejects_bad_signature(billing_module, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    payload = b'{"id":"evt_1","type":"customer.subscription.deleted","data":{"object":{"customer":"cus_1"}}}'
    # Wrong v1
    header = f"t={int(time.time())},v1=wrong"
    assert billing_module.verify_webhook(payload, header) is None


def test_verify_webhook_accepts_valid_signature(billing_module, monkeypatch):
    secret = "whsec_test"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    payload = json.dumps(
        {
            "id": "evt_42",
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_1"}},
        }
    ).encode()
    header, _ = _sign(secret, payload)

    event = billing_module.verify_webhook(payload, header)
    assert event is not None
    assert event.id == "evt_42"
    assert event.type == "customer.subscription.deleted"


def test_verify_webhook_handles_malformed_signature(billing_module, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    # No "t=" or "v1=" → safe rejection, not exception.
    assert billing_module.verify_webhook(b"{}", "garbage") is None
    assert billing_module.verify_webhook(b"{}", "") is None


# ---------------------------------------------------------------------------
# apply_webhook → tenant state changes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_webhook_suspends_on_payment_failed(billing_module, monkeypatch):
    from war_room.v1 import tenants

    tenant, _ = await tenants.create_tenant("Acme")
    # Pin a stripe_customer_id directly so the lookup matches.
    data = await tenants._read_all()
    data[tenant.id]["stripe_customer_id"] = "cus_xyz"
    await tenants._write_all(data)

    event = billing_module.WebhookEvent(
        id="evt_1",
        type="invoice.payment_failed",
        data={"object": {"customer": "cus_xyz"}},
    )
    outcome = await billing_module.apply_webhook(event)
    assert outcome == {"applied": True, "action": "suspended"}

    refreshed = await tenants.get_tenant(tenant.id)
    assert refreshed is not None
    assert refreshed.status == "suspended"


@pytest.mark.asyncio
async def test_apply_webhook_changes_plan_on_subscription_updated(billing_module):
    from war_room.v1 import tenants

    tenant, _ = await tenants.create_tenant("Acme", plan="free")
    data = await tenants._read_all()
    data[tenant.id]["stripe_customer_id"] = "cus_42"
    await tenants._write_all(data)

    event = billing_module.WebhookEvent(
        id="evt_2",
        type="customer.subscription.updated",
        data={"object": {"customer": "cus_42", "metadata": {"plan": "enterprise"}}},
    )
    outcome = await billing_module.apply_webhook(event)
    assert outcome["applied"] is True
    assert outcome["plan"] == "enterprise"

    refreshed = await tenants.get_tenant(tenant.id)
    assert refreshed is not None
    assert refreshed.plan == "enterprise"


@pytest.mark.asyncio
async def test_apply_webhook_ignores_unknown_event(billing_module):
    event = billing_module.WebhookEvent(id="evt_3", type="charge.refunded", data={})
    outcome = await billing_module.apply_webhook(event)
    assert outcome == {"applied": False, "type": "charge.refunded"}


# ---------------------------------------------------------------------------
# DLQ replay handler integration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_billing_dlq_replay_requeues_when_configured(billing_module, monkeypatch):
    from war_room.v1 import dlq_replay

    # With Stripe unconfigured, the replay handler must reject (no infinite
    # replay loop) so the DLQ keeps the row instead of silently dropping it.
    ok = await dlq_replay._HANDLERS["billing_usage"]({"tenant_id": "acme", "usage_kind": "meetings", "quantity": 1})
    assert ok is False

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    billing_module._LAST_FLUSH = time.time()
    ok = await dlq_replay._HANDLERS["billing_usage"]({"tenant_id": "acme", "usage_kind": "meetings", "quantity": 3})
    assert ok is True
    # And the usage record made it back into the in-process queue.
    assert billing_module.pending_count() == 1
