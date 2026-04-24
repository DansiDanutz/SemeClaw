"""Billing and tenant cost routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import (
    _COST_LEDGER,
    _METRICS,
    APP_VERSION,
    SEMECLAW_TENANT_ID,
    STRIPE_PRICE_PER_MEETING,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    _tenant_id,
)

router = APIRouter(tags=["billing"])


@router.get("/api/billing/status")
async def api_billing_status(request: Request):
    """Report billing configuration + current tenant usage. Lets consumers
    surface 'usage this month: $X' in their own UI even before Stripe is wired."""
    tenant = _tenant_id(request)
    usage = _COST_LEDGER.get(tenant, {})
    configured = bool(STRIPE_SECRET_KEY and STRIPE_PRICE_PER_MEETING)
    return JSONResponse(
        {
            "tenant_id": tenant,
            "stripe_configured": configured,
            "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY or None,
            "usage": usage,
            "suggested_pricing": {
                "model": "per_meeting",
                "est_cents_per_meeting": 25,
                "included_per_tier": {"trial": 10, "pro": 100, "team": 500},
            },
        }
    )


@router.post("/api/billing/report-usage")
async def api_billing_report_usage(request: Request):
    """Push this tenant's meeting count to Stripe as a metered subscription
    usage record. Returns 503 until Stripe is configured ??? scaffold only."""
    tenant = _tenant_id(request)
    if not (STRIPE_SECRET_KEY and STRIPE_PRICE_PER_MEETING):
        return JSONResponse(
            {
                "error": "stripe not configured",
                "required_env": ["STRIPE_SECRET_KEY", "STRIPE_PRICE_PER_MEETING"],
                "tenant_id": tenant,
            },
            status_code=503,
        )
    return JSONResponse({"ok": True, "scaffold_only": True, "tenant_id": tenant})


@router.get("/metrics")
async def metrics():
    """Prometheus exposition format. Scrape with Prometheus, Grafana Agent, etc."""
    from fastapi.responses import Response as FR

    lines = [
        "# HELP semeclaw_info SemeClaw agent info",
        "# TYPE semeclaw_info gauge",
        f'semeclaw_info{{version="{APP_VERSION}",tenant="{SEMECLAW_TENANT_ID}"}} 1',
    ]
    for k, v in _METRICS.items():
        lines.append(f"# HELP semeclaw_{k} Count of {k}")
        lines.append(f"# TYPE semeclaw_{k} counter")
        lines.append(f"semeclaw_{k}_total {v}")
    return FR("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
