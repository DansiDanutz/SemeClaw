"""Tenant lifecycle: create, read, soft-delete.

Storage is local JSON in ``data/v1/tenants.json`` so the demo runs with no
external dependency. Once Supabase credentials are set, the same module
can be re-pointed at a ``tenants`` table behind the same async API.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import asdict, dataclass, field
from typing import Literal

from war_room.v1 import storage as _s

PlanName = Literal["free", "pro", "enterprise"]
TenantStatus = Literal["active", "suspended", "deleted"]

DEFAULT_PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {"meetings_per_month": 10, "tts_chars_per_month": 50_000},
    "pro": {"meetings_per_month": 1000, "tts_chars_per_month": 5_000_000},
    "enterprise": {"meetings_per_month": 1_000_000, "tts_chars_per_month": 1_000_000_000},
}


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    api_key_hash: str
    plan: PlanName
    status: TenantStatus
    created_at: str
    deleted_at: str | None = None
    stripe_customer_id: str | None = None
    branding: dict = field(default_factory=dict)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _gen_id(prefix: str = "tnt") -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _gen_api_key() -> str:
    return f"sck_{secrets.token_urlsafe(32)}"


async def _read_all() -> dict[str, dict]:
    return await _s.read_json(_s.TENANTS_FILE, default={})


async def _write_all(data: dict[str, dict]) -> None:
    await _s.write_json(_s.TENANTS_FILE, data)


async def create_tenant(name: str, plan: PlanName = "free") -> tuple[Tenant, str]:
    """Returns (tenant_record, plain_api_key). The plain key is only shown
    once — afterwards only the hash is persisted.
    """
    if plan not in DEFAULT_PLAN_LIMITS:
        raise ValueError(f"unknown plan {plan!r}")
    api_key = _gen_api_key()
    tid = _gen_id()
    tenant = Tenant(
        id=tid,
        name=name.strip()[:120] or "(unnamed)",
        api_key_hash=_hash_key(api_key),
        plan=plan,
        status="active",
        created_at=_s.utcnow_iso(),
    )
    data = await _read_all()
    data[tid] = asdict(tenant)
    await _write_all(data)
    return tenant, api_key


async def list_tenants(include_deleted: bool = False) -> list[Tenant]:
    data = await _read_all()
    out = [Tenant(**row) for row in data.values()]
    if not include_deleted:
        out = [t for t in out if t.status != "deleted"]
    out.sort(key=lambda t: t.created_at, reverse=True)
    return out


async def get_tenant(tenant_id: str) -> Tenant | None:
    data = await _read_all()
    row = data.get(tenant_id)
    return Tenant(**row) if row else None


async def get_tenant_by_api_key(api_key: str) -> Tenant | None:
    """Constant-time scan; tenant counts here are admin-scale, not request-scale."""
    target = _hash_key(api_key)
    data = await _read_all()
    for row in data.values():
        # secrets.compare_digest avoids timing oracles on the comparison.
        if secrets.compare_digest(row.get("api_key_hash", ""), target):
            return Tenant(**row)
    return None


async def soft_delete(tenant_id: str) -> Tenant | None:
    data = await _read_all()
    if tenant_id not in data:
        return None
    data[tenant_id] = {
        **data[tenant_id],
        "status": "deleted",
        "deleted_at": _s.utcnow_iso(),
    }
    await _write_all(data)
    return Tenant(**data[tenant_id])


async def update_plan(tenant_id: str, plan: PlanName) -> Tenant | None:
    if plan not in DEFAULT_PLAN_LIMITS:
        raise ValueError(f"unknown plan {plan!r}")
    data = await _read_all()
    if tenant_id not in data:
        return None
    data[tenant_id] = {**data[tenant_id], "plan": plan}
    await _write_all(data)
    return Tenant(**data[tenant_id])


def plan_limits(plan: PlanName) -> dict[str, int]:
    return dict(DEFAULT_PLAN_LIMITS[plan])
