"""Advertiser console API — projects, library, card generation, wallet, checkout."""
from __future__ import annotations

import os
import ipaddress
import logging
import secrets
import socket
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import httpx

from war_room.dashboard.routes.deps import (
    SUPA_URL,
    SUPA_KEY,
    SUPA_HEADERS,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    SEMECLAW_PUBLIC_URL,
    ADCLAW_PUBLIC_URL,
    require_advertiser_owner,
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
_SUPABASE_ANON_KEY = (
    os.environ.get("SUPABASE_ANON_KEY", "").strip()
    or os.environ.get("DLS_TEAM_SUPABASE_ANON_KEY", "").strip()
)

@router.get("/api/advertiser/auth/config")
async def api_advertiser_auth_config():
    """Return Supabase auth configuration so the frontend can initialise the JS client."""
    return JSONResponse({
        "supabase_url": SUPA_URL or os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip(),
        "supabase_anon_key": _SUPABASE_ANON_KEY,
        "redirect_url": f"{ADCLAW_PUBLIC_URL}/advertiser",
        "providers": ["google", "github"],
    })


# ---------------------------------------------------------------------------
# Auto-create advertiser row if missing (Supabase Auth user → adclaw_advertisers)
# ---------------------------------------------------------------------------
async def _ensure_advertiser(advertiser_id: str, email: str = "") -> None:
    """Idempotently create an advertiser row with the 10-credit welcome grant."""
    try:
        await _supa("post", "rpc/adclaw_ensure_wallet", json={
            "p_advertiser_id": advertiser_id,
            "p_email": email or "",
        })
    except Exception as e:
        logger.warning("ensure_advertiser failed: %s", e)


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/wallet")
async def api_advertiser_wallet(advertiser_id: str, request: Request):
    try:
        await require_advertiser_owner(request, advertiser_id)
        await _ensure_advertiser(advertiser_id)
        # Daily login bonus — RPC is idempotent per UTC day, safe to call on every fetch.
        try:
            await _supa("post", "rpc/adclaw_daily_login_bonus",
                        json={"p_advertiser_id": advertiser_id})
        except Exception as e:
            logger.debug("daily_login_bonus skipped: %s", e)
        rows = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=id,email,wallet_credits,is_subscribed,sub_expires_at,tier")
        if not rows:
            return JSONResponse({"id": advertiser_id, "wallet_credits": 0, "is_subscribed": False, "sub_expires_at": None, "tier": "free"}, status_code=200)
        return JSONResponse(rows[0])
    except Exception as e:
        logger.warning("wallet error: %s", e)
        return JSONResponse({"id": advertiser_id, "wallet_credits": 0, "is_subscribed": False, "error": str(e)}, status_code=200)


@router.post("/api/advertiser/{advertiser_id}/topup")
async def api_advertiser_topup(advertiser_id: str, request: Request):
    """Add credits to wallet. Called by Stripe webhook after payment."""
    await require_advertiser_owner(request, advertiser_id)
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
async def api_advertiser_projects(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    try:
        rows = await _supa(
            "get",
            f"adclaw_projects?advertiser_id=eq.{advertiser_id}"
            "&select=id,name,github_url,webpage_url,description,notes,ad_goal,target_audience,problem_solved,tagline,created_at"
            "&order=created_at.desc",
        )
        return JSONResponse(rows)
    except Exception as e:
        logger.warning("projects list error: %s", e)
        return JSONResponse([])


@router.post("/api/advertiser/{advertiser_id}/projects")
async def api_advertiser_create_project(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    try:
        await _ensure_advertiser(advertiser_id)
        rows = await _supa("post", "adclaw_projects", json={
            "advertiser_id": advertiser_id,
            "name": name,
            "github_url": (body.get("github_url") or "").strip(),
            "webpage_url": (body.get("webpage_url") or "").strip(),
            "description": (body.get("description") or "").strip(),
            "notes": (body.get("notes") or "").strip(),
            "ad_goal": (body.get("ad_goal") or "").strip(),
            "target_audience": (body.get("target_audience") or "").strip(),
            "problem_solved": (body.get("problem_solved") or "").strip(),
            "tagline": (body.get("tagline") or "").strip(),
            "voice": (body.get("voice") or "af_bella").strip(),
        })
        return JSONResponse(rows[0] if rows else {"id": "", "name": name}, status_code=201)
    except Exception as e:
        logger.warning("create project error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/advertiser/{advertiser_id}/projects/{project_id}")
async def api_advertiser_update_project(advertiser_id: str, project_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    body = await request.json()
    try:
        # Verify ownership
        rows = await _supa("get", f"adclaw_projects?id=eq.{project_id}&advertiser_id=eq.{advertiser_id}&select=id")
        if not rows:
            return JSONResponse({"error": "project not found"}, status_code=404)
        patch = {}
        for k in ("name", "github_url", "webpage_url", "description", "notes", "ad_goal", "target_audience", "problem_solved", "tagline", "voice"):
            if k in body:
                patch[k] = (body[k] or "").strip()
        if patch:
            await _supa("patch", f"adclaw_projects?id=eq.{project_id}", json=patch)
        return JSONResponse({"ok": True, "project_id": project_id})
    except Exception as e:
        logger.warning("update project error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Library (slides)
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/library")
async def api_advertiser_library(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    try:
        # 1. Get all campaign IDs for this advertiser
        campaigns = await _supa(
            "get",
            f"adclaw_campaigns?advertiser_id=eq.{advertiser_id}&select=id",
        )
        if not campaigns:
            return JSONResponse([])
        campaign_ids = ",".join(c["id"] for c in campaigns)

        # 2. Get slides for those campaigns
        rows = await _supa(
            "get",
            f"adclaw_slides?campaign_id=in.({campaign_ids})"
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
    webpage = project.get("webpage_url") or ""

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
        "cta_url": webpage or url or "#",
        "accent_color": "#f59e0b",
        "hook": hook,
        "hook_highlight": name,
        "tagline": f"For {audience}" if audience else "",
        "_source": "backend",
    }


GENERATE_CARD_COST = 10  # credits to generate one ad card

@router.post("/api/advertiser/{advertiser_id}/projects/{project_id}/generate-card")
async def api_advertiser_generate_card(advertiser_id: str, project_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    try:
        rows = await _supa("get", f"adclaw_projects?id=eq.{project_id}&advertiser_id=eq.{advertiser_id}&select=*")
        if not rows:
            return JSONResponse({"error": "project not found"}, status_code=404)

        # Deduct credits atomically (RPC checks balance and rejects if insufficient)
        try:
            result = await _supa("post", "rpc/adclaw_deduct_credits", json={
                "p_advertiser_id": advertiser_id,
                "p_credits":       GENERATE_CARD_COST,
                "p_description":   f"Generated ad card for project {project_id[:8]}",
            })
            rd = result if isinstance(result, dict) else (result[0] if result else {})
            if rd.get("ok") is False:
                return JSONResponse({
                    "error": rd.get("error", "insufficient credits"),
                    "cost":  GENERATE_CARD_COST,
                    "wallet_credits": rd.get("wallet_credits", 0),
                }, status_code=402)
        except Exception as e:
            logger.warning("deduct_credits failed, continuing: %s", e)

        draft = _compose_draft(rows[0])
        return JSONResponse({"draft": draft, "cost": GENERATE_CARD_COST})
    except Exception as e:
        logger.warning("generate-card error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Slides CRUD
# ---------------------------------------------------------------------------
@router.post("/api/advertiser/{advertiser_id}/slides")
async def api_advertiser_create_slide(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    body = await request.json()
    campaign = (body.get("campaign") or "").strip() or "Generated"
    try:
        await _ensure_advertiser(advertiser_id)
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
            "status": (body.get("status") or "active").strip(),
        })
        return JSONResponse(rows[0] if rows else {"ok": True}, status_code=201)
    except Exception as e:
        logger.warning("create slide error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/advertiser/{advertiser_id}/slides/{slide_id}/edit")
async def api_advertiser_edit_slide(advertiser_id: str, slide_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
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
@router.post("/api/advertiser/{advertiser_id}/slides/{slide_id}/status")
async def api_advertiser_slide_status(advertiser_id: str, slide_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
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

        # Active-card cap enforcement. Both "enabled" and "active" statuses put
        # the slide into the meeting-room serving pool; cap the combined count.
        prev_status = rows[0].get("status")
        promoting = status in ("enabled", "active") and prev_status not in ("enabled", "active")
        if promoting:
            chk = await _supa("post", "rpc/adclaw_can_activate_slide",
                              json={"p_advertiser_id": advertiser_id})
            chk_d = chk if isinstance(chk, dict) else (chk[0] if chk else {})
            if not chk_d.get("ok", True):
                return JSONResponse({
                    "error": chk_d.get("error", "active card cap reached"),
                    "tier":  chk_d.get("tier"),
                    "cap":   chk_d.get("cap"),
                    "active": chk_d.get("active"),
                }, status_code=409)

        await _supa("patch", f"adclaw_slides?id=eq.{slide_id}", json={"status": status})
        return JSONResponse({"ok": True, "slide_id": slide_id, "status": status})
    except Exception as e:
        logger.warning("slide status error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Credit history
# ---------------------------------------------------------------------------
@router.get("/api/advertiser/{advertiser_id}/history")
async def api_advertiser_history(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    try:
        # Summary stats
        adv_rows = await _supa(
            "get",
            f"adclaw_advertisers?id=eq.{advertiser_id}&select=wallet_credits"
        )
        wallet = adv_rows[0]["wallet_credits"] if adv_rows else 0

        tx_rows = await _supa(
            "get",
            f"adclaw_credit_transactions?advertiser_id=eq.{advertiser_id}&select=*&order=created_at.desc&limit=200"
        )

        total_purchased = sum(t["credits"] for t in tx_rows if t["type"] in ("purchase", "subscription", "refund", "adjustment") and t["credits"] > 0)
        total_spent = sum(-t["credits"] for t in tx_rows if t["type"] == "spend" and t["credits"] < 0)

        # Enrich spend rows with slide headline + campaign name
        slide_ids = [t["slide_id"] for t in tx_rows if t.get("slide_id")]
        campaign_ids = [t["campaign_id"] for t in tx_rows if t.get("campaign_id")]

        slide_map = {}
        if slide_ids:
            try:
                s_rows = await _supa(
                    "get",
                    f"adclaw_slides?id=in.({','.join(slide_ids)})&select=id,headline"
                )
                slide_map = {s["id"]: s["headline"] for s in s_rows}
            except Exception:
                pass

        camp_map = {}
        if campaign_ids:
            try:
                c_rows = await _supa(
                    "get",
                    f"adclaw_campaigns?id=in.({','.join(campaign_ids)})&select=id,name"
                )
                camp_map = {c["id"]: c["name"] for c in c_rows}
            except Exception:
                pass

        transactions = []
        for t in tx_rows:
            meta = t.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    import json
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            transactions.append({
                "id": t["id"],
                "type": t["type"],
                "credits": t["credits"],
                "description": t["description"],
                "slide_headline": slide_map.get(t.get("slide_id"), ""),
                "campaign_name": camp_map.get(t.get("campaign_id"), ""),
                "instance_id": meta.get("instance_id", ""),
                "created_at": t["created_at"],
            })

        return JSONResponse({
            "wallet_credits": wallet,
            "total_purchased": total_purchased,
            "total_spent": total_spent,
            "transactions": transactions,
        })
    except Exception as e:
        logger.warning("history error: %s", e)
        return JSONResponse({"wallet_credits": 0, "total_purchased": 0, "total_spent": 0, "transactions": []})


# ---------------------------------------------------------------------------
# Stripe checkout
# ---------------------------------------------------------------------------
@router.post("/api/advertiser/{advertiser_id}/checkout")
async def api_advertiser_checkout(advertiser_id: str, request: Request):
    await require_advertiser_owner(request, advertiser_id)
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "stripe not configured"}, status_code=503)
    body = await request.json()
    tier = body.get("tier", "standard")  # standard | subscription
    try:
        await _ensure_advertiser(advertiser_id)
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

        # Map tier label → Stripe Price ID.
        # Accepts: "gold", "diamond", "25", "50" (legacy), "subscription" (legacy → diamond).
        _tier_price_env = {
            "gold":    "ADCLAW_PRICE_USD_25",
            "25":      "ADCLAW_PRICE_USD_25",
            "diamond": "ADCLAW_PRICE_USD_50",
            "50":      "ADCLAW_PRICE_USD_50",
            "subscription": "ADCLAW_PRICE_USD_50",
        }
        _tier_canonical = {
            "gold": "gold", "25": "gold",
            "diamond": "diamond", "50": "diamond", "subscription": "diamond",
        }
        if tier in _tier_price_env:
            price_env = _tier_price_env[tier]
            price_id = os.environ.get(price_env, "").strip() or os.environ.get("STRIPE_PRICE_SUBSCRIPTION", "").strip()
            if not price_id:
                return JSONResponse({"error": f"subscription price not configured ({price_env})"}, status_code=503)
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=f"{ADCLAW_PUBLIC_URL}/advertiser?checkout=success",
                cancel_url=f"{ADCLAW_PUBLIC_URL}/advertiser?checkout=cancel",
                metadata={
                    "advertiser_id": advertiser_id,
                    "tier": _tier_canonical.get(tier, "diamond"),
                },
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
                success_url=f"{ADCLAW_PUBLIC_URL}/advertiser?checkout=success",
                cancel_url=f"{ADCLAW_PUBLIC_URL}/advertiser?checkout=cancel",
                metadata={"advertiser_id": advertiser_id, "credits": str(credits)},
            )

        return JSONResponse({"url": session.url, "session_id": session.id})
    except Exception as e:
        logger.warning("checkout error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Stripe webhook — handles subscription lifecycle (renewal, cancellation, failure)
# ---------------------------------------------------------------------------
@router.post("/api/advertiser/stripe/webhook")
@router.post("/api/advertiser/webhook/stripe")
async def api_advertiser_stripe_webhook(request: Request):
    """
    Stripe subscription lifecycle:
      invoice.paid                   → extend sub_expires_at, set tier from price
      invoice.payment_failed         → downgrade to free
      customer.subscription.deleted  → downgrade to free
    """
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "stripe not configured"}, status_code=503)

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.warning("stripe webhook signature failed: %s", e)
        return JSONResponse({"error": "invalid signature"}, status_code=400)

    etype = event.get("type", "")
    obj   = event.get("data", {}).get("object", {}) or {}

    # Resolve advertiser_id — from subscription metadata, else lookup by customer.
    async def _adv_from_customer(customer_id: str) -> Optional[str]:
        if not customer_id:
            return None
        rows = await _supa("get", f"adclaw_advertisers?stripe_customer_id=eq.{customer_id}&select=id")
        return rows[0]["id"] if rows else None

    try:
        if etype == "invoice.paid":
            customer_id = obj.get("customer")
            advertiser_id = await _adv_from_customer(customer_id)
            if not advertiser_id:
                return JSONResponse({"ok": True, "note": "no matching advertiser"})
            # Tier from price id on the line item
            tier = "diamond"
            try:
                line = (obj.get("lines", {}).get("data") or [{}])[0]
                price_id = (line.get("price") or {}).get("id", "")
                if price_id and price_id == os.environ.get("ADCLAW_PRICE_USD_25", "").strip():
                    tier = "gold"
            except Exception:
                pass
            # Period end (unix seconds) → ISO
            period_end = obj.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
            expires_iso = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat() if period_end else None
            patch = {"tier": tier, "is_subscribed": True}
            if expires_iso:
                patch["sub_expires_at"] = expires_iso
            await _supa("patch", f"adclaw_advertisers?id=eq.{advertiser_id}", json=patch)
            # Grant subscription credits: Gold 300 (250+50), Diamond 750 (500+250)
            await _supa("post", "rpc/adclaw_grant_subscription_credits",
                        json={"p_advertiser_id": advertiser_id, "p_tier": tier})

        elif etype == "checkout.session.completed":
            # One-time credit top-up: $1 = 10 credits. Amount comes from metadata.credits
            # (set by /checkout). Only fires for mode=payment sessions.
            if obj.get("mode") == "payment":
                meta = obj.get("metadata") or {}
                advertiser_id = meta.get("advertiser_id")
                try:
                    credits = int(meta.get("credits", 0))
                except Exception:
                    credits = 0
                if advertiser_id and credits > 0:
                    await _supa("post", "rpc/adclaw_record_topup", json={
                        "p_advertiser_id": advertiser_id,
                        "p_credits":       credits,
                        "p_stripe_id":     obj.get("id"),
                    })

        elif etype in ("invoice.payment_failed", "customer.subscription.deleted"):
            customer_id = obj.get("customer")
            advertiser_id = await _adv_from_customer(customer_id)
            if advertiser_id:
                await _supa("post", "rpc/adclaw_downgrade_to_free",
                            json={"p_advertiser_id": advertiser_id})

    except Exception as e:
        logger.warning("stripe webhook handler error (%s): %s", etype, e)
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True, "type": etype})


# ═══════════════════════════════════════════════════════════════════════════
# SPECIAL — revenue features (priority boost, targeting, referrals, analytics)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/advertiser/{advertiser_id}/special")
async def api_advertiser_special(advertiser_id: str):
    """Bundled fetch for the Special tab: referral info + slide analytics."""
    try:
        adv = await _supa("get",
            f"adclaw_advertisers?id=eq.{advertiser_id}"
            f"&select=id,referral_code,referred_by,wallet_credits,tier")
        if not adv:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Referral tree — list of referred users with week_spent + total earned
        try:
            tree = await _supa("post", "rpc/adclaw_referral_tree",
                               json={"p_advertiser_id": advertiser_id})
        except Exception as e:
            logger.warning("referral_tree rpc failed: %s", e)
            tree = []
        # Total bonus this advertiser has received from referrals
        try:
            paid_rows = await _supa("get",
                f"adclaw_credit_transactions?advertiser_id=eq.{advertiser_id}"
                f"&description=like.Weekly%20referral%20bonus%25&select=credits")
            weekly_bonus_paid = sum(int(r.get("credits") or 0) for r in (paid_rows or []))
        except Exception:
            weekly_bonus_paid = 0
        analytics = await _supa("post", "rpc/adclaw_slide_analytics",
                                json={"p_advertiser_id": advertiser_id})
        return JSONResponse({
            "referral_code":     adv[0].get("referral_code"),
            "referred_count":    len(tree or []),
            "referred_by":       adv[0].get("referred_by"),
            "referred_users":    tree or [],
            "weekly_bonus_paid": weekly_bonus_paid,
            "referred_earned":   weekly_bonus_paid,
            "tier":              adv[0].get("tier", "free"),
            "wallet_credits":    adv[0].get("wallet_credits", 0),
            "slides":            analytics or [],
        })
    except Exception as e:
        logger.warning("special fetch error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/advertiser/{advertiser_id}/apply-referral")
async def api_advertiser_apply_referral(advertiser_id: str, request: Request):
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"error": "code required"}, status_code=400)
    try:
        res = await _supa("post", "rpc/adclaw_apply_referral",
                          json={"p_advertiser_id": advertiser_id, "p_ref_code": code})
        rd = res if isinstance(res, dict) else (res[0] if res else {})
        status = 200 if rd.get("ok") else 400
        return JSONResponse(rd, status_code=status)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/advertiser/{advertiser_id}/slides/{slide_id}/boost")
@router.post("/api/advertiser/{advertiser_id}/slides/{slide_id}/boost")
async def api_advertiser_slide_boost(advertiser_id: str, slide_id: str, request: Request):
    """Toggle priority_boost on a slide. Diamond-only feature."""
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    try:
        # Tier gate — priority boost is a Diamond perk
        adv = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=tier")
        tier = (adv[0].get("tier") if adv else "free") or "free"
        if enabled and tier != "diamond":
            return JSONResponse({"error": "Priority boost is a Diamond-tier perk",
                                 "tier": tier}, status_code=403)
        # Ownership check
        rows = await _supa("get",
            f"adclaw_slides?id=eq.{slide_id}"
            f"&select=id,campaign:adclaw_campaigns(advertiser_id)")
        if not rows or rows[0].get("campaign", {}).get("advertiser_id") != advertiser_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        await _supa("patch", f"adclaw_slides?id=eq.{slide_id}",
                    json={"priority_boost": enabled})
        return JSONResponse({"ok": True, "priority_boost": enabled})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/advertiser/{advertiser_id}/slides/{slide_id}/targeting")
@router.post("/api/advertiser/{advertiser_id}/slides/{slide_id}/targeting")
async def api_advertiser_slide_targeting(advertiser_id: str, slide_id: str, request: Request):
    """Set target_keywords[] for a slide. Gold+Diamond only."""
    body = await request.json()
    kws = body.get("keywords") or []
    if isinstance(kws, str):
        kws = [k.strip().lower() for k in kws.split(",") if k.strip()]
    else:
        kws = [str(k).strip().lower() for k in kws if str(k).strip()]
    kws = kws[:10]  # cap
    try:
        adv = await _supa("get", f"adclaw_advertisers?id=eq.{advertiser_id}&select=tier")
        tier = (adv[0].get("tier") if adv else "free") or "free"
        if tier == "free":
            return JSONResponse({"error": "Targeting requires Gold or Diamond",
                                 "tier": tier}, status_code=403)
        rows = await _supa("get",
            f"adclaw_slides?id=eq.{slide_id}"
            f"&select=id,campaign:adclaw_campaigns(advertiser_id)")
        if not rows or rows[0].get("campaign", {}).get("advertiser_id") != advertiser_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        await _supa("patch", f"adclaw_slides?id=eq.{slide_id}",
                    json={"target_keywords": kws})
        return JSONResponse({"ok": True, "keywords": kws})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Click tracking — public, called from the meeting room when a viewer clicks a card.
@router.post("/api/spotlight/click")
async def api_spotlight_click(request: Request):
    body = await request.json()
    slide_id = (body.get("slide_id") or "").strip()
    if not slide_id:
        return JSONResponse({"error": "slide_id required"}, status_code=400)
    try:
        ip = (request.headers.get("x-forwarded-for") or
              (request.client.host if request.client else "")).split(",")[0].strip()
        import hashlib
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:32] if ip else ""
        res = await _supa("post", "rpc/adclaw_record_click",
                          json={"p_slide_id": slide_id, "p_ip_hash": ip_hash})
        return JSONResponse(res if isinstance(res, dict) else (res[0] if res else {"ok": True}))
    except Exception as e:
        logger.warning("click record failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Webpage fetch proxy
#
# Client-side analysis needs to read an advertiser-supplied product URL to
# extract title/description/body text when building an ad draft. Direct
# browser fetch is usually blocked by CORS, so we proxy it through the
# backend with an SSRF guard, size cap, and short timeout. This replaces the
# previous dependency on the public api.allorigins.win CORS proxy.
# ---------------------------------------------------------------------------
_FETCH_WEBPAGE_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
_FETCH_WEBPAGE_TIMEOUT_S = 10.0
_FETCH_WEBPAGE_UA = "SemeClawAdvertiser/1.0 (+https://ad-semeclaw.vercel.app)"


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    # Block common cloud metadata endpoints explicitly.
    if ip in {"169.254.169.254", "fd00:ec2::254"}:
        return False
    return True


def _resolve_and_check(host: str) -> Optional[str]:
    """Resolve host and reject if any resolved IP is not publicly routable.

    Note: does not defeat DNS rebinding. For defence-in-depth, requests should
    be made against the resolved IP with Host header preserved — left as a
    follow-up. This still closes the trivial SSRF vectors (localhost, RFC1918,
    link-local metadata, etc.) that the previous public proxy had.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "unresolvable"
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            return f"private/reserved IP not allowed: {ip}"
    return None


@router.get("/api/advertiser/{advertiser_id}/fetch-webpage")
async def api_advertiser_fetch_webpage(advertiser_id: str, request: Request):
    """Proxy-fetch an advertiser-supplied URL so the client can analyse it.

    Query param:
        url: absolute http(s) URL to fetch.

    Returns the raw response body as text. Refuses URLs that resolve to
    private/loopback/link-local/metadata addresses, non-http(s) schemes, or
    responses larger than 2 MiB.
    """
    raw_url = request.query_params.get("url", "").strip()
    if not raw_url:
        return JSONResponse({"error": "missing url"}, status_code=400)

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return JSONResponse({"error": "invalid url"}, status_code=400)

    if parsed.scheme not in ("http", "https"):
        return JSONResponse({"error": "only http(s) URLs allowed"}, status_code=400)
    if not parsed.hostname:
        return JSONResponse({"error": "missing host"}, status_code=400)

    guard = _resolve_and_check(parsed.hostname)
    if guard:
        return JSONResponse({"error": guard}, status_code=400)

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_WEBPAGE_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": _FETCH_WEBPAGE_UA},
        ) as client:
            async with client.stream("GET", raw_url) as resp:
                if resp.status_code >= 400:
                    return JSONResponse(
                        {"error": f"upstream {resp.status_code}"},
                        status_code=502,
                    )
                ctype = resp.headers.get("content-type", "")
                if ctype and "html" not in ctype and "text" not in ctype and "xml" not in ctype:
                    return JSONResponse(
                        {"error": f"unsupported content-type: {ctype}"},
                        status_code=415,
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > _FETCH_WEBPAGE_MAX_BYTES:
                        return JSONResponse(
                            {"error": "response too large"},
                            status_code=413,
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                # Best-effort decode; fall back to latin-1 so we never raise here.
                try:
                    text = body.decode(resp.encoding or "utf-8", errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                return PlainTextResponse(text, media_type="text/html; charset=utf-8")
    except httpx.HTTPError as e:
        logger.warning("fetch-webpage httpx error for %s: %s", raw_url, e)
        return JSONResponse({"error": "fetch failed"}, status_code=502)
    except Exception as e:
        logger.warning("fetch-webpage unexpected error for %s: %s", raw_url, e)
        return JSONResponse({"error": "fetch failed"}, status_code=500)
