"""Stripe integration for NERVIX credit purchases."""

from __future__ import annotations

import logging
import os

import stripe

from nervix_platform.models import CreditTier

logger = logging.getLogger(__name__)

# Use test keys by default
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "pk_test_")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Credit tier configuration
TIER_CONFIG: dict[CreditTier, dict] = {
    CreditTier.STARTER: {
        "name": "Starter",
        "credits": 100,
        "price_cents": 500,  # $5.00
    },
    CreditTier.PRO: {
        "name": "Pro",
        "credits": 500,
        "price_cents": 1900,  # $19.00
    },
    CreditTier.TEAM: {
        "name": "Team",
        "credits": 2000,
        "price_cents": 4900,  # $49.00
    },
}


def get_publishable_key() -> str:
    return STRIPE_PUBLISHABLE_KEY


def create_customer(email: str, name: str) -> stripe.Customer:
    """Create a Stripe customer."""
    return stripe.Customer.create(email=email, name=name)


def create_checkout_session(
    customer_id: str,
    tier: CreditTier,
    success_url: str,
    cancel_url: str,
    client_reference_id: str,
) -> stripe.checkout.Session:
    """Create a Stripe Checkout Session for credit purchase."""
    config = TIER_CONFIG[tier]
    return stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"NERVIX {config['name']} Credits",
                        "description": f"{config['credits']} ad-play credits",
                    },
                    "unit_amount": config["price_cents"],
                },
                "quantity": 1,
            }
        ],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=client_reference_id,
        metadata={
            "tier": tier.value,
            "credits": str(config["credits"]),
        },
    )


def construct_event(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and construct a Stripe webhook event."""
    if not WEBHOOK_SECRET:
        # In test mode without webhook secret, parse directly
        import json

        return stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    return stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)


def get_tier_credits(tier: CreditTier) -> int:
    return TIER_CONFIG[tier]["credits"]
