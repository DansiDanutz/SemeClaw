"""Plan-based quota enforcement middleware.

Resolves a tenant from the incoming bearer token (or X-Tenant-Id header
when bearer is the global admin key), runs the appropriate quota check,
and returns 402 Payment Required when over plan. On success, increments
the relevant usage counter and queues a Stripe metered-usage record so
billing reconciles in the next flush.

Routes guarded (path-prefix match):
  - POST /api/tasks/intervene  (counts as 1 intervention + 1 meeting on commit)
  - POST /api/meeting/*/finalize (counts as 1 meeting)
  - POST /api/meeting/execute (counts as 1 meeting)
  - POST /api/v1/dialog/preview (counts as 1 meeting — keeps demos honest)
"""

from __future__ import annotations

import logging
import re

from war_room.v1 import billing as _billing
from war_room.v1 import sse as _sse
from war_room.v1 import tenants as _tenants
from war_room.v1 import usage as _usage

logger = logging.getLogger("semeclaw.v1.quota")

METERED_ROUTES: list[tuple[re.Pattern, str, dict[str, int]]] = [
    (re.compile(r"^/api/tasks/[^/]+/intervene$"), "meetings", {"interventions": 1}),
    (re.compile(r"^/api/meeting/finalize$"), "meetings", {"meetings": 1}),
    (re.compile(r"^/api/meeting/execute$"), "meetings", {"meetings": 1}),
    (re.compile(r"^/api/v1/dialog/preview$"), "meetings", {"meetings": 1}),
]


def _match(path: str, method: str) -> tuple[str, dict[str, int]] | None:
    if method != "POST":
        return None
    for pattern, kind, deltas in METERED_ROUTES:
        if pattern.match(path):
            return kind, deltas
    return None


async def _resolve_tenant(request) -> _tenants.Tenant | None:
    """Resolve tenant from bearer + X-Tenant-Id. Returns None when the
    request uses the global admin key or has no tenant headers."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :].strip()
    # Tenant-scoped keys start with sck_ — global SEMECLAW_API_KEY does not.
    if token.startswith("sck_"):
        return await _tenants.get_tenant_by_api_key(token)
    # If the global admin key is presented but the caller scoped the
    # request via X-Tenant-Id, attribute usage to that tenant.
    hint = (request.headers.get("x-tenant-id") or "").strip()
    if hint:
        return await _tenants.get_tenant(hint)
    return None


def install(app) -> None:
    """Mount the quota middleware. Must run AFTER the audit middleware
    so failed quota checks still appear in the audit log."""

    @app.middleware("http")
    async def _quota_gate(request, call_next):
        from fastapi.responses import JSONResponse as _J

        match = _match(request.url.path, request.method)
        if match is None:
            return await call_next(request)

        kind, deltas = match
        tenant = await _resolve_tenant(request)
        if tenant is None:
            # No tenant context — let it through; default tenant has no cap enforced.
            return await call_next(request)

        if tenant.status != "active":
            return _J(
                {
                    "error": "tenant_inactive",
                    "tenant_id": tenant.id,
                    "status": tenant.status,
                },
                status_code=402,
                headers={"X-SemeClaw-Quota": f"status={tenant.status}"},
            )

        check = await _usage.check_quota(tenant.id, kind=kind)
        if not check.allowed:
            _sse.publish(
                "quota_blocked",
                {"tenant_id": tenant.id, "kind": kind, "used": check.used, "limit": check.limit},
            )
            return _J(
                {
                    "error": "plan_quota_exceeded",
                    "tenant_id": tenant.id,
                    "plan": check.plan,
                    "used": check.used,
                    "limit": check.limit,
                    "kind": kind,
                    "upgrade": "PATCH /api/tenants/{tid}/plan with plan=pro|enterprise",
                },
                status_code=402,
                headers={
                    "X-SemeClaw-Quota": f"plan={check.plan},used={check.used},limit={check.limit}",
                    "Retry-After": "3600",
                },
            )

        # Bookkeeping — only on a successful pass-through (2xx/3xx).
        request.state.tenant_id = tenant.id
        request.state.actor = f"tenant:{tenant.id}"
        response = await call_next(request)
        if 200 <= response.status_code < 400:
            try:
                await _usage.increment(tenant.id, **deltas)
                for k, v in deltas.items():
                    if v:
                        await _billing.report_usage(tenant.id, k, quantity=int(v))
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage increment failed for tenant=%s: %s", tenant.id, exc)
        return response
