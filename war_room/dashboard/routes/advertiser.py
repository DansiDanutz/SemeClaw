"""Advertiser console API — projects, library, card generation, wallet, checkout."""
from __future__ import annotations

import os
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import httpx

from war_room.dashboard.routes.deps import (
    SUPA_URL,
    SUPA_KEY,
    SUPA_HEADERS,
    STRIPE_SECRET_KEY,
    SEMECLAW_PUBLIC_URL,
)

logger = logging.getLogger("war_room.dashboard.advertiser")

router = APIRouter(tags=["advertiser"])

# ---------------------------------------------------------------------------
# Supabase helper
# ---------------------------------------------------------------------------
async def _supa(method: str, path: str, **kwargs):
    if not SUPA_URL or not SUPA_KEY:
        raise RuntimeError("Supabase not configured")
    async with httpx.AsyncClient(base_url=SUPA_URL, timeout=10.0) as c:
        r = await getattr(c, method)(f"/rest/v1/{path}", headers=SUPA_HEADERS, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else []


# ---------------------------------------------------------------------------
# Auth config (public — no bearer required)
# ---------------------------------------------------------------------------
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

@router.get("/api/advertiser/auth/config")
async def api_advertiser_auth_config():
    """Return Supabase auth configuration so the frontend can initialise the JS client."""
    return JSONResponse({
        "supabase_url": SUPA_URL or os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip(),
        "supabase_anon_key": _SUPABASE_ANON_KEY,
        "redirect_url": f"{SEMECLAW_PUBLIC_URL}/advertiser",
        "providers": ["google", "github"],
    })


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/wallet")
async def api_advertiser_wallet(advertiser_id: str):
    try:
        rows = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=id,email,wallet_credits,is_subscribed,sub_expires_at")
        if not rows:
            return JSONResponse({"id": advertiser_id, "wallet_credits": 0, "is_subscribed": False, "sub_expires_at": None}, status_code=200)
        return JSONResponse(rows[0])
    except Exception as e:
        logger.warning("wallet error: %s", e)
        return JSONResponse({"id": advertiser_id, "wallet_credits": 0, "is_subscribed": False, "error": str(e)}, status_code=200)


@router.post("/api/advertiser/{advertiser_id}/topup")
async def api_advertiser_topup(advertiser_id: str, request: Request):
    """Add credits to wallet. Called by Stripe webhook after payment."""
    body = await request.json()
    credits = int(body.get("credits", 0))
    if credits <= 0:
        return JSONResponse({"error": "credits must be > 0"}, status_code=400)
    try:
        await _supa("post", "rpc/adclaw_topup_credits", json={
            "p_advertiser_id": advertiser_id,
            "p_credits": credits,
        })
        return JSONResponse({"ok": True, "credits_added": credits})
    except Exception as e:
        logger.warning("topup error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/projects")
async def api_advertiser_projects(advertiser_id: str):
    try:
        rows = await _supa(
            "get",
            f"adclaw_projects?advertiser_id=eq.{advertiser_id}"
            "&select=id,name,github_url,description,ad_goal,target_audience,created_at"
            "&order=created_at.desc",
        )
        return JSONResponse(rows)
    except Exception as e:
        logger.warning("projects list error: %s", e)
        return JSONResponse([])


@router.post("/api/advertiser/{advertiser_id}/projects")
async def api_advertiser_create_project(advertiser_id: str, request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    try:
        rows = await _supa("post", "adclaw_projects", json={
            "advertiser_id": advertiser_id,
            "name": name,
            "github_url": (body.get("github_url") or "").strip(),
            "description": (body.get("description") or "").strip(),
            "ad_goal": (body.get("ad_goal") or "").strip(),
            "target_audience": (body.get("target_audience") or "").strip(),
        })
        return JSONResponse(rows[0] if rows else {"id": "", "name": name}, status_code=201)
    except Exception as e:
        logger.warning("create project error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Library (slides)
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/library")
async def api_advertiser_library(advertiser_id: str):
    try:
        # Slides belong to campaigns which belong to advertisers
        rows = await _supa(
            "get",
            f"adclaw_slides?campaign_id=in.("
            f"select id from adclaw_campaigns where advertiser_id=eq.{advertiser_id}"
            f")"
            "&select=*,campaign:adclaw_campaigns(name)"
            "&order=created_at.desc",
        )
        # Normalise campaign name into a flat field for the frontend
        out = []
        for r in rows:
            item = dict(r)
            campaign = item.pop("campaign", None)
            item["campaign_name"] = campaign.get("name", "—") if campaign else "—"
            out.append(item)
        return JSONResponse(out)
    except Exception as e:
        logger.warning("library error: %s", e)
        return JSONResponse([])


# ---------------------------------------------------------------------------
# Card generation
# ---------------------------------------------------------------------------
def _compose_draft(project: dict) -> dict:
    """Server-side template fallback for card generation."""
    name = project.get("name") or "Project"
    desc = (project.get("description") or "").strip()
    goal = (project.get("ad_goal") or "").strip()
    audience = (project.get("target_audience") or "").strip()
    url = project.get("github_url") or ""

    headline = goal or f"Meet {name}"
    hook = f"Looking for what *{name}* does?"
    if "ai" in (name + " " + desc).lower() or "agent" in (name + " " + desc).lower():
        hook = f"Own an AI tool? *{name}* can put it to work."

    body = desc
    if len(body) > 200:
        body = body[:197] + "…"
    if not body:
        body = "Built for teams who need real results, fast."

    return {
        "headline": headline,
        "badge": "Featured",
        "body": body,
        "cta_label": "Learn more",
        "cta_url": url or "#",
        "accent_color": "#f59e0b",
        "hook": hook,
        "hook_highlight": name,
        "tagline": f"For {audience}" if audience else "",
        "_source": "backend",
    }


@router.post("/api/advertiser/{advertiser_id}/projects/{project_id}/generate-card")
async def api_advertiser_generate_card(advertiser_id: str, project_id: str):
    try:
        rows = await _supa("get", f"adclaw_projects?id=eq.{project_id}&advertiser_id=eq.{advertiser_id}&select=*")
        if not rows:
            return JSONResponse({"error": "project not found"}, status_code=404)
        draft = _compose_draft(rows[0])
        return JSONResponse({"draft": draft})
    except Exception as e:
        logger.warning("generate-card error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Slides CRUD
# ---------------------------------------------------------------------------
@router.post("/api/advertiser/{advertiser_id}/slides")
async def api_advertiser_create_slide(advertiser_id: str, request: Request):
    body = await request.json()
    campaign = (body.get("campaign") or "").strip() or "Generated"
    try:
        # Ensure a campaign exists for this advertiser
        camp_rows = await _supa("get", f"adclaw_campaigns?advertiser_id=eq.{advertiser_id}&name=eq.{campaign}&select=id")
        if camp_rows:
            campaign_id = camp_rows[0]["id"]
        else:
            camp_rows = await _supa("post", "adclaw_campaigns", json={
                "advertiser_id": advertiser_id,
                "name": campaign,
                "status": "active",
            })
            campaign_id = camp_rows[0]["id"] if camp_rows else None

        if not campaign_id:
            raise RuntimeError("could not create campaign")

        rows = await _supa("post", "adclaw_slides", json={
            "campaign_id": campaign_id,
            "slide_type": body.get("slide_type", "feature"),
            "headline": (body.get("headline") or "").strip(),
            "body": (body.get("body") or "").strip(),
            "badge": (body.get("badge") or "Featured").strip(),
            "accent_color": body.get("accent", "#f59e0b"),
            "cta_label": (body.get("cta_label") or "").strip(),
            "cta_url": (body.get("cta_url") or "").strip(),
            "status": "active",
        })
        return JSONResponse(rows[0] if rows else {"ok": True}, status_code=201)
    except Exception as e:
        logger.warning("create slide error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/advertiser/{advertiser_id}/slides/{slide_id}/edit")
async def api_advertiser_edit_slide(advertiser_id: str, slide_id: str, request: Request):
    body = await request.json()
    try:
        # Verify ownership via campaign → advertiser
        rows = await _supa(
            "get",
            f"adclaw_slides?id=eq.{slide_id}&select=*,campaign:adclaw_campaigns(advertiser_id)"
        )
        if not rows:
            return JSONResponse({"error": "slide not found"}, status_code=404)
        owner = rows[0].get("campaign", {}).get("advertiser_id")
        if owner != advertiser_id:
            return JSONResponse({"error": "not found"}, status_code=404)

        patch = {}
        for k in ("headline", "body", "badge", "accent_color", "cta_label", "cta_url"):
            if k in body:
                patch[k] = body[k]
        if patch:
            patch["last_edited_at"] = datetime.now(timezone.utc).isoformat()
            await _supa("patch", f"adclaw_slides?id=eq.{slide_id}", json=patch)

        return JSONResponse({"ok": True, "slide_id": slide_id})
    except Exception as e:
        logger.warning("edit slide error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/advertiser/{advertiser_id}/slides/{slide_id}/status")
async def api_advertiser_slide_status(advertiser_id: str, slide_id: str, request: Request):
    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in ("enabled", "active", "inactive", "pending", "paused"):
        return JSONResponse({"error": "invalid status"}, status_code=400)
    try:
        rows = await _supa(
            "get",
            f"adclaw_slides?id=eq.{slide_id}&select=*,campaign:adclaw_campaigns(advertiser_id)"
        )
        if not rows:
            return JSONResponse({"error": "slide not found"}, status_code=404)
        owner = rows[0].get("campaign", {}).get("advertiser_id")
        if owner != advertiser_id:
            return JSONResponse({"error": "not found"}, status_code=404)

        await _supa("patch", f"adclaw_slides?id=eq.{slide_id}", json={"status": status})
        return JSONResponse({"ok": True, "slide_id": slide_id, "status": status})
    except Exception as e:
        logger.warning("slide status error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Stripe checkout
# ---------------------------------------------------------------------------
@router.post("/api/advertiser/{advertiser_id}/checkout")
async def api_advertiser_checkout(advertiser_id: str, request: Request):
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "stripe not configured"}, status_code=503)
    body = await request.json()
    tier = body.get("tier", "standard")  # standard | subscription
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        # Lookup or create Stripe customer
        adv_rows = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=email,stripe_customer_id")
        adv = adv_rows[0] if adv_rows else {}
        customer_id = adv.get("stripe_customer_id")

        if not customer_id:
            customer = stripe.Customer.create(
                email=adv.get("email", ""),
                metadata={"advertiser_id": advertiser_id},
            )
            customer_id = customer.id
            await _supa("patch", f"adclaw_advertisers?id=eq.{advertiser_id}", json={"stripe_customer_id": customer_id})

        if tier == "subscription":
            price_id = os.environ.get("STRIPE_PRICE_SUBSCRIPTION", "").strip()
            if not price_id:
                return JSONResponse({"error": "subscription price not configured"}, status_code=503)
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=f"{SEMECLAW_PUBLIC_URL}/advertiser?checkout=success",
                cancel_url=f"{SEMECLAW_PUBLIC_URL}/advertiser?checkout=cancel",
            )
        else:
            # One-time credit top-up
            credits = int(body.get("credits", 100))
            unit_amount = max(100, credits * 10)  # $1 = 10 credits, in cents
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": f"{credits} Ad Credits"},
                        "unit_amount": unit_amount,
                    },
                    "quantity": 1,
                }],
                success_url=f"{SEMECLAW_PUBLIC_URL}/advertiser?checkout=success",
                cancel_url=f"{SEMECLAW_PUBLIC_URL}/advertiser?checkout=cancel",
                metadata={"advertiser_id": advertiser_id, "credits": str(credits)},
            )

        return JSONResponse({"url": session.url, "session_id": session.id})
    except Exception as e:
        logger.warning("checkout error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
