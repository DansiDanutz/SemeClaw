from __future__ import annotations

from war_room.dashboard.routes import advertiser


def test_annual_checkout_uses_distinct_prices_and_canonical_tiers(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("ADCLAW_PRICE_USD_250", "price_gold_annual")
    monkeypatch.setenv("ADCLAW_PRICE_USD_500", "price_diamond_annual")

    assert advertiser._subscription_price_id("gold_annual") == (
        "ADCLAW_PRICE_USD_250",
        "price_gold_annual",
    )
    assert advertiser._subscription_price_id("diamond_annual") == (
        "ADCLAW_PRICE_USD_500",
        "price_diamond_annual",
    )
    assert advertiser._SUBSCRIPTION_TIER_CANONICAL["gold_annual"] == "gold"
    assert advertiser._SUBSCRIPTION_TIER_CANONICAL["diamond_annual"] == "diamond"


def test_annual_checkout_fails_closed_without_dedicated_price(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SUBSCRIPTION", "price_monthly_fallback")
    monkeypatch.delenv("ADCLAW_PRICE_USD_250", raising=False)

    assert advertiser._subscription_price_id("gold_annual") == (
        "ADCLAW_PRICE_USD_250",
        "",
    )


def test_annual_gold_webhook_classification(monkeypatch):
    monkeypatch.setenv("ADCLAW_PRICE_USD_25", "price_gold_monthly")
    monkeypatch.setenv("ADCLAW_PRICE_USD_250", "price_gold_annual")

    assert advertiser._subscription_tier_from_price("price_gold_annual") == "gold"
    assert advertiser._subscription_tier_from_price("price_diamond_annual") == "diamond"
    assert advertiser._subscription_benefit_months_from_price("price_gold_annual") == 12
    assert advertiser._subscription_benefit_months_from_price("price_diamond_annual") == 1


def test_monthly_subscription_grants_one_benefit_month(monkeypatch):
    monkeypatch.setenv("ADCLAW_PRICE_USD_25", "price_gold_monthly")
    monkeypatch.setenv("ADCLAW_PRICE_USD_250", "price_gold_annual")

    assert advertiser._subscription_benefit_months_from_price("price_gold_monthly") == 1
