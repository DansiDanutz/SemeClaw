"""Per-tenant usage tracking + plan quota enforcement.

Counters are stored in ``data/v1/usage.json`` keyed by
``{tenant_id}/{YYYY-MM}``. Increments are atomic via the shared per-file
asyncio lock from ``storage.py``. The numbers feed two surfaces:

  - ``GET /api/tenants/{tid}/usage`` — public per-tenant summary
  - ``GET /api/admin/v1/usage`` — admin-wide rollup with quota status

Plan limits come from ``tenants.plan_limits``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from war_room.v1 import storage as _s
from war_room.v1 import tenants as _tenants

_COUNTABLE_KEYS = ("meetings", "tts_chars", "llm_tokens", "interventions")


def _period_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _composite_key(tenant_id: str, period: str | None = None) -> str:
    return f"{tenant_id}/{period or _period_key()}"


async def _read() -> dict[str, dict]:
    return await _s.read_json(_s.USAGE_FILE, default={})


async def _write(data: dict[str, dict]) -> None:
    await _s.write_json(_s.USAGE_FILE, data)


async def increment(tenant_id: str, **deltas: int) -> dict[str, int]:
    """Atomic-ish per-tenant counter bump (single writer per file via lock)."""
    if not tenant_id:
        tenant_id = "default"
    async with _s._lock_for(_s.USAGE_FILE):
        data = await _read()
        key = _composite_key(tenant_id)
        bucket = dict(data.get(key) or {})
        for name, amount in deltas.items():
            if name not in _COUNTABLE_KEYS:
                continue
            bucket[name] = int(bucket.get(name, 0)) + int(amount)
        bucket["updated_at"] = _s.utcnow_iso()
        data[key] = bucket
        # Caller already holds the lock — use the un-locking writer to
        # avoid the non-reentrant asyncio.Lock deadlock.
        _s._write_json_unlocked(_s.USAGE_FILE, data)
        return bucket


async def snapshot(tenant_id: str, period: str | None = None) -> dict:
    data = await _read()
    bucket = data.get(_composite_key(tenant_id, period)) or {}
    return {k: int(bucket.get(k, 0)) for k in _COUNTABLE_KEYS} | {
        "tenant_id": tenant_id,
        "period": period or _period_key(),
        "updated_at": bucket.get("updated_at"),
    }


async def rollup(period: str | None = None) -> list[dict]:
    """Return per-tenant usage rolled up to one row per tenant for the period."""
    p = period or _period_key()
    data = await _read()
    out: list[dict] = []
    for key, bucket in data.items():
        try:
            tid, period_key = key.split("/", 1)
        except ValueError:
            continue
        if period_key != p:
            continue
        out.append(
            {
                "tenant_id": tid,
                "period": p,
                **{k: int(bucket.get(k, 0)) for k in _COUNTABLE_KEYS},
                "updated_at": bucket.get("updated_at"),
            }
        )
    out.sort(key=lambda r: r["meetings"], reverse=True)
    return out


@dataclass(frozen=True)
class QuotaCheck:
    allowed: bool
    reason: str
    plan: str
    used: int
    limit: int


async def check_quota(tenant_id: str, *, kind: str = "meetings") -> QuotaCheck:
    """Return whether the tenant is within plan for the given counter.

    ``kind`` is one of the keys in plan_limits (currently ``meetings`` and
    ``tts_chars``). Tenants we have never seen are treated as the 'free'
    plan so the gate fails closed rather than silently letting traffic through.
    """
    snap = await snapshot(tenant_id)
    tenant = await _tenants.get_tenant(tenant_id)
    if tenant is None:
        plan = "free"
        limits = _tenants.plan_limits("free")
    else:
        plan = tenant.plan
        limits = _tenants.plan_limits(tenant.plan)
    key = "meetings_per_month" if kind == "meetings" else ("tts_chars_per_month" if kind == "tts_chars" else None)
    if key is None or key not in limits:
        return QuotaCheck(True, "untracked_counter", plan, 0, 0)
    used = snap.get(kind, 0)
    limit = limits[key]
    if used < limit:
        return QuotaCheck(True, "within_plan", plan, used, limit)
    return QuotaCheck(False, "plan_cap_reached", plan, used, limit)
