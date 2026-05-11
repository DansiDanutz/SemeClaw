"""Outbound webhooks — tenant subscriptions + HMAC-signed delivery + DLQ.

Subscriptions live in ``data/v1/webhook_subscriptions.json`` keyed by id.
Each subscription:
  {
    "id": "wsub_…",
    "tenant_id": "tnt_…",
    "url": "https://customer.example/hook",
    "secret": "whsec_…",  # used for HMAC-SHA256 over the request body
    "events": ["meeting.finalized", "intervention.recorded", ...]
                # or ["*"] to receive everything
    "status": "active" | "disabled",
    "created_at": "...",
    "last_delivery_at": "...",
    "last_status": int | null
  }

Delivery:
  - Best-effort, fire-and-forget via ``asyncio.create_task``.
  - HTTP timeout 10s. Successful = 2xx.
  - On failure, the payload + URL + signature header are appended to the
    ``webhooks`` DLQ (already registered with a replay handler).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from war_room.v1 import storage as _s

logger = logging.getLogger("semeclaw.v1.webhooks")

SUBSCRIPTIONS_FILE_REL = "webhook_subscriptions.json"
DLQ_FILENAME = "webhooks_dlq.jsonl"


def _subs_path() -> Path:
    return Path(_s._data_dir()) / SUBSCRIPTIONS_FILE_REL


def _dlq_path() -> Path:
    return Path(_s._data_dir()) / DLQ_FILENAME


@dataclass(frozen=True)
class WebhookSubscription:
    id: str
    tenant_id: str
    url: str
    secret: str
    events: tuple[str, ...]
    status: str = "active"
    created_at: str = ""
    last_delivery_at: str | None = None
    last_status: int | None = None


async def _read_all() -> dict[str, dict]:
    return await _s.read_json(_subs_path(), default={})


async def _write_all(data: dict[str, dict]) -> None:
    await _s.write_json(_subs_path(), data)


def _gen_id(prefix: str = "wsub") -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _gen_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(24)}"


def _row_to_sub(row: dict) -> WebhookSubscription:
    fields = {f for f in WebhookSubscription.__dataclass_fields__}
    safe = {k: v for k, v in row.items() if k in fields}
    safe["events"] = tuple(safe.get("events") or [])
    return WebhookSubscription(**safe)


async def subscribe(*, tenant_id: str, url: str, events: list[str]) -> WebhookSubscription:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    if not events:
        raise ValueError("events cannot be empty (use ['*'] for all)")
    sub = WebhookSubscription(
        id=_gen_id(),
        tenant_id=tenant_id,
        url=url,
        secret=_gen_secret(),
        events=tuple(events),
        status="active",
        created_at=_s.utcnow_iso(),
    )
    data = await _read_all()
    data[sub.id] = asdict(sub)
    # asdict serialises the tuple to a list — JSON-friendly.
    await _write_all(data)
    return sub


async def list_subscriptions(tenant_id: str | None = None) -> list[WebhookSubscription]:
    data = await _read_all()
    rows = [_row_to_sub(row) for row in data.values()]
    if tenant_id:
        rows = [s for s in rows if s.tenant_id == tenant_id]
    rows.sort(key=lambda s: s.created_at, reverse=True)
    return rows


async def unsubscribe(sub_id: str) -> bool:
    data = await _read_all()
    if sub_id not in data:
        return False
    del data[sub_id]
    await _write_all(data)
    return True


def _sign(secret: str, body: bytes, ts: int) -> str:
    msg = f"{ts}.".encode() + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _matches(sub_events: tuple[str, ...], event: str) -> bool:
    return "*" in sub_events or event in sub_events


async def _record_outcome(sub_id: str, *, status: int) -> None:
    data = await _read_all()
    if sub_id not in data:
        return
    data[sub_id]["last_delivery_at"] = _s.utcnow_iso()
    data[sub_id]["last_status"] = int(status)
    await _write_all(data)


async def _post_one(sub: WebhookSubscription, event: str, payload: dict) -> None:
    body = json.dumps({"event": event, "data": payload, "ts": _s.utcnow_iso()}, separators=(",", ":")).encode()
    ts = int(time.time())
    sig = _sign(sub.secret, body, ts)
    headers = {
        "Content-Type": "application/json",
        "X-SemeClaw-Event": event,
        "X-SemeClaw-Subscription": sub.id,
        "X-SemeClaw-Signature": f"t={ts},v1={sig}",
    }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(sub.url, content=body, headers=headers)
            await _record_outcome(sub.id, status=r.status_code)
            if r.status_code >= 400:
                _enqueue_dlq(sub, event, body, headers, reason=f"http_{r.status_code}")
    except Exception as exc:  # noqa: BLE001
        await _record_outcome(sub.id, status=0)
        _enqueue_dlq(sub, event, body, headers, reason=f"transport_{exc.__class__.__name__}")


def _enqueue_dlq(sub: WebhookSubscription, event: str, body: bytes, headers: dict, *, reason: str) -> None:
    path = _dlq_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "kind": "webhook_delivery",
        "subscription_id": sub.id,
        "tenant_id": sub.tenant_id,
        "event": event,
        "url": sub.url,
        "body": body.decode("utf-8", "replace"),
        "headers": headers,
        "reason": reason,
        "attempt": 0,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def dispatch(event: str, payload: dict, *, tenant_id: str | None = None) -> int:
    """Fan an event out to every matching subscription. Returns the number
    of deliveries scheduled (not the number of successes — those land
    asynchronously)."""
    subs = await list_subscriptions(tenant_id=tenant_id)
    scheduled = 0
    for sub in subs:
        if sub.status != "active":
            continue
        if tenant_id and sub.tenant_id != tenant_id:
            continue
        if not _matches(sub.events, event):
            continue
        asyncio.create_task(_post_one(sub, event, payload))
        scheduled += 1
    return scheduled
