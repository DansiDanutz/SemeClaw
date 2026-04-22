"""NERVIX Platform — FastAPI app for member sign-up, Stripe credits, dashboard, ad ingestion."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nervix_platform.database import db
from nervix_platform.models import (
    AdPlayEvent,
    CheckoutSessionRequest,
    CreditTier,
    CreditTransaction,
    DashboardStats,
    Member,
    Project,
)
from nervix_platform.stripe_client import (
    create_checkout_session,
    create_customer,
    get_publishable_key,
    get_tier_credits,
    construct_event,
)

logger = logging.getLogger(__name__)

# In-memory dedup set for Stripe webhook event ids (retries within process lifetime)
_processed_stripe_events: set[str] = set()

# Templates and static files
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure data directory exists."""
    from pathlib import Path

    Path(__file__).parent.joinpath("data").mkdir(exist_ok=True)
    yield


app = FastAPI(
    title="NERVIX Platform",
    description="Member sign-up, credit purchase, project dashboard, ad ingestion",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """NERVIX landing page."""
    return templates.TemplateResponse(request, "landing.html", {"stripe_key": get_publishable_key()})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    """Member sign-up page."""
    return templates.TemplateResponse(request, "signup.html", {})


@app.post("/api/members")
async def create_member_api(email: str = Form(...), name: str = Form(...)):
    """Create a new member."""
    existing = db.get_member_by_email(email)
    if existing:
        return JSONResponse({"member_id": existing["id"], "message": "Member already exists"}, status_code=200)

    member = Member(email=email, name=name)
    db.create_member(member.model_dump())
    return {"member_id": member.id, "message": "Member created"}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, member_id: str):
    """Member dashboard."""
    member = db.get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    projects = db.list_projects(member_id)
    transactions = db.list_transactions(member_id)
    ad_plays = db.get_ad_plays()
    total_ad_plays = sum(1 for e in ad_plays if any(p["id"] == e["project_id"] for p in projects))
    total_clicks = sum(p.get("clicks", 0) for p in projects)

    stats = DashboardStats(
        member=Member(**member),
        projects=[Project(**p) for p in projects],
        total_ad_plays=total_ad_plays,
        total_clicks=total_clicks,
        credit_balance=member.get("credits", 0),
        recent_transactions=[CreditTransaction(**t) for t in transactions[:10]],
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "stripe_key": get_publishable_key(),
        },
    )


@app.post("/api/projects")
async def create_project_api(
    member_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    url: str = Form(None),
):
    """Submit a new project."""
    member = db.get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    project = Project(member_id=member_id, title=title, description=description, url=url)
    db.create_project(project.model_dump())
    return {"project_id": project.id, "message": "Project created"}


@app.post("/api/checkout")
async def create_checkout(data: CheckoutSessionRequest, member_id: str):
    """Create a Stripe checkout session."""
    member = db.get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if not member.get("stripe_customer_id"):
        customer = create_customer(member["email"], member["name"])
        member = db.update_member(member_id, {"stripe_customer_id": customer.id})

    session = create_checkout_session(
        customer_id=member["stripe_customer_id"],
        tier=data.tier,
        success_url=data.success_url,
        cancel_url=data.cancel_url,
        client_reference_id=member_id,
    )

    return {"session_id": session.id, "url": session.url}


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = construct_event(payload, sig_header)
    except ValueError as e:
        logger.error(f"Stripe webhook configuration error: {e}")
        raise HTTPException(status_code=503, detail="Webhook verification not configured")
    except Exception as e:
        logger.warning(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Idempotency: skip duplicate event ids
    event_id = event.get("id")
    if event_id and event_id in _processed_stripe_events:
        return {"status": "already_processed"}
    if event_id:
        _processed_stripe_events.add(event_id)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        member_id = session.get("client_reference_id")
        metadata = session.get("metadata", {})
        tier = metadata.get("tier")
        credits = int(metadata.get("credits", 0))

        if member_id and credits:
            tx = CreditTransaction(
                member_id=member_id,
                amount=credits,
                description=f"Purchase: {tier}",
                stripe_payment_intent_id=session.get("payment_intent"),
            )
            db.add_transaction(tx.model_dump())
            logger.info(f"Credited {credits} to member {member_id}")

    return {"status": "ok"}


@app.post("/api/ad-play")
async def ingest_ad_play(event: AdPlayEvent):
    """Ingest an ad play event and deduct credits."""
    project = db.get_project(event.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    member = db.get_member(project["member_id"])
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Deduct 1 credit per completed ad play; partial plays deferred (fractional credits)
    cost = -1 if event.completed else 0
    if cost < 0:
        if not db.try_debit_credits(member["id"], abs(cost)):
            raise HTTPException(status_code=402, detail="Insufficient credits")
        tx = CreditTransaction(
            member_id=member["id"],
            amount=cost,
            description=f"Ad play: {project['title']}",
        )
        db.add_transaction(tx.model_dump(), update_credits=False)

    db.add_ad_play(event.model_dump())

    return {"status": "accepted", "credits_deducted": abs(cost)}


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "nervix-platform"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("nervix_platform.main:app", host="0.0.0.0", port=8001, reload=True)
