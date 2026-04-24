"""Pydantic models for NERVIX platform."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class CreditTier(str, Enum):
    """Available credit purchase tiers."""

    STARTER = "starter"
    PRO = "pro"
    TEAM = "team"


class Member(BaseModel):
    """A NERVIX platform member."""

    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M%S"))
    email: EmailStr
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    credits: int = 0
    stripe_customer_id: str | None = None


class Project(BaseModel):
    """A project submitted by a member."""

    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M%S"))
    member_id: str
    title: str
    description: str
    url: str | None = None
    status: str = "pending"  # pending, active, completed, archived
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    ad_plays: int = 0
    clicks: int = 0


class AdPlayEvent(BaseModel):
    """An ad play event ingested from SemeClaw."""

    project_id: str
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_seconds: float = 0.0
    completed: bool = False


class CreditTransaction(BaseModel):
    """A credit transaction (purchase or deduction)."""

    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M%S"))
    member_id: str
    amount: int  # positive for purchase, negative for usage
    description: str
    stripe_payment_intent_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CheckoutSessionRequest(BaseModel):
    """Request to create a Stripe checkout session."""

    tier: CreditTier
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(BaseModel):
    """Response with Stripe checkout URL."""

    session_id: str
    url: str


class DashboardStats(BaseModel):
    """Aggregated dashboard statistics for a member."""

    member: Member
    projects: list[Project]
    total_ad_plays: int
    total_clicks: int
    credit_balance: int
    recent_transactions: list[CreditTransaction]
