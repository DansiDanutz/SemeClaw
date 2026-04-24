"""Retention helpers — soft 100-task cap + over-cap suggestions."""
from __future__ import annotations

from . import _db


CAP = 100


SUGGESTIONS = [
    "Archive completed tasks (status=done) — they're kept but no longer count toward the cap.",
    "Spin up a second tenant: pass `x-tenant-id` header to the API for project-specific buckets.",
    "Run `semeclaw tasks gc` weekly to enforce the cap automatically.",
    "Upgrade to a self-hosted Postgres for unlimited retention (the cap is a soft limit only).",
]


async def quota(tenant_id: str = "default") -> dict:
    return await _db.quota(tenant_id)


async def enforce(tenant_id: str = "default", cap: int = CAP) -> dict:
    return await _db.enforce_retention(tenant_id, cap)


async def status(tenant_id: str = "default") -> dict:
    q = await quota(tenant_id)
    over = q.get("over_cap", 0) if isinstance(q, dict) else 0
    out = {"quota": q, "over_cap": over}
    if over and over > 0:
        out["suggestions"] = SUGGESTIONS
    return out
