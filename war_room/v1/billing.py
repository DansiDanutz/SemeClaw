"""Stripe metered billing — real wire-up (not a scaffold).

Surfaces:
  - ``ensure_customer(tenant)`` — idempotent Stripe customer provisioning.
  - ``report_usage(tenant_id, kind, quantity)`` — queue a metered-usage
    record for a 60-second batched flush.
  - ``flush()`` — drain the queue to Stripe (or to the metered-usage
    DLQ if Stripe rejects).
  - ``verify_webhook(payload, signature)`` — HMAC-verify a Stripe
    webhook and return the parsed event.

Zero-key safety: every public coroutine no-ops gracefully when
``STRIPE_SECRET_KEY`` is unset. That keeps the open-source clone running
without billing wiring, and makes the unit tests deterministic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from war_room.v1 import storage as _s
from war_room.v1 import tenants as _tenants

logger = logging.getLogger("semeclaw.v1.billing")

STRIPE_API = "https://api.stripe.com/v1"
USAGE_DLQ = "billing_usage"  # registered in war_room.dashboard.server _DLQ_REGISTRY


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_configured() -> bool:
    return bool(_env("STRIPE_SECRET_KEY"))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_env('STRIPE_SECRET_KEY')}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


# ---------------------------------------------------------------------------
# Customer provisioning
# ---------------------------------------------------------------------------
async def ensure_customer(tenant: _tenants.Tenant) -> str | None:
    """Return a Stripe customer id for the tenant, creating one if needed.
    Persists the id back into the tenant record so subsequent calls are
    no-ops. Returns None when Stripe is not configured.
    """
    if not is_configured():
        return None
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id

    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a hard dep elsewhere
        return None

    body = {
        "name": tenant.name,
        "metadata[tenant_id]": tenant.id,
        "metadata[plan]": tenant.plan,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{STRIPE_API}/customers", headers=_headers(), data=body)
        if r.status_code >= 400:
            logger.warning("stripe customer create failed: %s", r.text[:240])
            return None
        cid = (r.json() or {}).get("id")

    if cid:
        # Persist on the tenant record.
        data = await _tenants._read_all()
        row = data.get(tenant.id)
        if row is not None:
            row["stripe_customer_id"] = cid
            data[tenant.id] = row
            await _tenants._write_all(data)
    return cid


# ---------------------------------------------------------------------------
# Metered usage flush — batched
# ---------------------------------------------------------------------------
@dataclass
class _UsageRecord:
    tenant_id: str
    kind: str  # "meetings" | "tts_chars" | "llm_tokens"
    quantity: int
    ts: int  # unix seconds


_PENDING: list[_UsageRecord] = []
_LAST_FLUSH = time.time()
_FLUSH_INTERVAL = 60  # seconds


async def report_usage(tenant_id: str, kind: str, quantity: int = 1) -> None:
    """Queue a metered-usage delta. Cheap, in-memory, fans out on flush()."""
    if quantity <= 0:
        return
    _PENDING.append(_UsageRecord(tenant_id=tenant_id, kind=kind, quantity=int(quantity), ts=int(time.time())))
    # Opportunistic flush so a quiet system still posts within the window.
    global _LAST_FLUSH
    if time.time() - _LAST_FLUSH >= _FLUSH_INTERVAL:
        await flush()


def _price_id_for(kind: str) -> str:
    return (
        _env(
            {
                "meetings": "STRIPE_PRICE_METER_MEETINGS",
                "tts_chars": "STRIPE_PRICE_METER_TTS",
                "llm_tokens": "STRIPE_PRICE_METER_LLM",
            }.get(kind, "")
        )
        or ""
    )


async def flush() -> dict[str, int]:
    """Drain the pending queue. Failures land in the billing DLQ for replay."""
    global _LAST_FLUSH
    # Atomic swap: snapshot current queue, leave a fresh empty list.
    pending = list(_PENDING)
    _PENDING.clear()

    if not pending:
        _LAST_FLUSH = time.time()
        return {"flushed": 0, "queued": 0, "dlq": 0}

    if not is_configured():
        # Save everything to the DLQ so we can replay once keys land.
        for rec in pending:
            _enqueue_dlq(rec, reason="stripe_not_configured")
        _LAST_FLUSH = time.time()
        return {"flushed": 0, "queued": len(pending), "dlq": len(pending)}

    try:
        import httpx
    except Exception:
        for rec in pending:
            _enqueue_dlq(rec, reason="httpx_missing")
        return {"flushed": 0, "queued": len(pending), "dlq": len(pending)}

    flushed = 0
    dlq = 0
    async with httpx.AsyncClient(timeout=10.0) as c:
        for rec in pending:
            tenant = await _tenants.get_tenant(rec.tenant_id)
            if tenant is None:
                _enqueue_dlq(rec, reason="tenant_missing")
                dlq += 1
                continue
            sub_item = _env(f"STRIPE_SUB_ITEM_{rec.kind.upper()}_{tenant.plan.upper()}")
            if not sub_item:
                # No subscription-item id mapped — fall back to DLQ.
                _enqueue_dlq(rec, reason="no_subscription_item")
                dlq += 1
                continue
            body = {
                "quantity": str(rec.quantity),
                "timestamp": str(rec.ts),
                "action": "increment",
            }
            try:
                r = await c.post(
                    f"{STRIPE_API}/subscription_items/{sub_item}/usage_records",
                    headers=_headers(),
                    data=body,
                )
                if r.status_code >= 400:
                    _enqueue_dlq(rec, reason=f"stripe_{r.status_code}")
                    dlq += 1
                else:
                    flushed += 1
            except Exception as exc:  # noqa: BLE001
                _enqueue_dlq(rec, reason=f"transport_{exc.__class__.__name__}")
                dlq += 1

    _LAST_FLUSH = time.time()
    return {"flushed": flushed, "queued": len(pending), "dlq": dlq}


def _enqueue_dlq(rec: _UsageRecord, *, reason: str) -> None:
    path = _s._data_dir() / "billing_usage_dlq.jsonl"
    line = json.dumps(
        {
            "kind": "billing_usage",
            "tenant_id": rec.tenant_id,
            "usage_kind": rec.kind,
            "quantity": rec.quantity,
            "ts": rec.ts,
            "reason": reason,
            "attempt": 0,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pending_count() -> int:
    return len(_PENDING)


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WebhookEvent:
    id: str
    type: str
    data: dict[str, Any]


def verify_webhook(payload: bytes, signature_header: str) -> WebhookEvent | None:
    """Verify a Stripe webhook signature.

    Stripe sends ``Stripe-Signature: t=<timestamp>,v1=<hmac>``. We
    recompute HMAC-SHA256(secret, "t.payload") and compare in constant
    time. Returns the parsed event on success, None on any failure.
    """
    secret = _env("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return None
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    ts = parts.get("t")
    sig = parts.get("v1")
    if not (ts and sig):
        return None
    signed = f"{ts}.{payload.decode('utf-8', 'replace')}".encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    return WebhookEvent(
        id=body.get("id", ""),
        type=body.get("type", ""),
        data=body.get("data") or {},
    )


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------
async def apply_webhook(event: WebhookEvent) -> dict:
    """Translate a Stripe event into a tenant state change.

    Only the events we actually act on land here. Anything else is logged
    and acknowledged so Stripe stops retrying.
    """
    if event.type == "customer.subscription.deleted":
        cid = (event.data.get("object") or {}).get("customer")
        await _suspend_by_customer(cid, reason="subscription_deleted")
        return {"applied": True, "action": "suspended"}
    if event.type == "invoice.payment_failed":
        cid = (event.data.get("object") or {}).get("customer")
        await _suspend_by_customer(cid, reason="payment_failed")
        return {"applied": True, "action": "suspended"}
    if event.type == "customer.subscription.updated":
        sub = event.data.get("object") or {}
        cid = sub.get("customer")
        plan = (sub.get("metadata") or {}).get("plan")
        if cid and plan:
            await _set_plan_by_customer(cid, plan)
            return {"applied": True, "action": "plan_updated", "plan": plan}
    return {"applied": False, "type": event.type}


async def _suspend_by_customer(customer_id: str | None, *, reason: str) -> None:
    if not customer_id:
        return
    data = await _tenants._read_all()
    for tid, row in list(data.items()):
        if row.get("stripe_customer_id") == customer_id:
            row["status"] = "suspended"
            row["last_billing_event"] = reason
            data[tid] = row
            await _tenants._write_all(data)
            return


async def _set_plan_by_customer(customer_id: str, plan: str) -> None:
    if plan not in _tenants.DEFAULT_PLAN_LIMITS:
        return
    data = await _tenants._read_all()
    for tid, row in list(data.items()):
        if row.get("stripe_customer_id") == customer_id:
            row["plan"] = plan
            data[tid] = row
            await _tenants._write_all(data)
            return
