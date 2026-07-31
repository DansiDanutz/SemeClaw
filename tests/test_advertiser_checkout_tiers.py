from __future__ import annotations

from pathlib import Path

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
    monkeypatch.setenv("ADCLAW_PRICE_USD_500", "price_diamond_annual")

    assert advertiser._subscription_tier_from_price("price_gold_annual") == "gold"
    assert advertiser._subscription_tier_from_price("price_diamond_annual") == "diamond"
    assert advertiser._subscription_tier_from_price("price_unknown") is None
    assert advertiser._subscription_benefit_months_from_price("price_gold_annual") == 12
    assert advertiser._subscription_benefit_months_from_price("price_diamond_annual") == 12


def test_monthly_subscription_grants_one_benefit_month(monkeypatch):
    monkeypatch.setenv("ADCLAW_PRICE_USD_25", "price_gold_monthly")
    monkeypatch.setenv("ADCLAW_PRICE_USD_250", "price_gold_annual")

    assert advertiser._subscription_benefit_months_from_price("price_gold_monthly") == 1


def test_paid_generation_never_renders_an_unconfirmed_fallback():
    repo_root = Path(__file__).resolve().parents[1]
    rendered_blocks = []
    for relative in ("ad-semeclaw/advertiser.html", "ad-semeclaw/index.html"):
        source = (repo_root / relative).read_text(encoding="utf-8")
        start = source.index("// Stage 5: Generate the paid draft")
        end = source.index("// Stage 6: Render", start)
        block = source[start:end]
        rendered_blocks.append(block)
        assert "fetchBackendDraft(projectId)" in block
        assert "fetchOllamaDraft" not in block
        assert "composeDraftClientSide" not in block
        assert "No fallback was created" in block
        assert "throw e" in block

    assert rendered_blocks[0] == rendered_blocks[1]


def test_paid_operations_use_database_idempotency_contracts():
    repo_root = Path(__file__).resolve().parents[1]
    route = (repo_root / "war_room/dashboard/routes/advertiser.py").read_text(encoding="utf-8")
    migration = (
        repo_root / "war_room/db/migrations/2026_07_31_adclaw_idempotency.sql"
    ).read_text(encoding="utf-8")

    assert "rpc/adclaw_generate_card_once" in route
    assert "rpc/adclaw_grant_subscription_invoice_credits" in route
    assert '"p_invoice_id": invoice_id' in route
    assert '"p_request_id": request_id' in route
    assert 'for _ in range(benefit_months)' not in route
    assert "pg_advisory_xact_lock" in migration
    assert "adclaw_stripe_credit_grants" in migration
    assert "adclaw_generated_drafts" in migration
    assert "already_processed" in migration
    assert "for update" in migration.lower()


def test_generation_request_identity_survives_ambiguous_responses():
    repo_root = Path(__file__).resolve().parents[1]
    sources = [
        (repo_root / relative).read_text(encoding="utf-8")
        for relative in ("ad-semeclaw/advertiser.html", "ad-semeclaw/index.html")
    ]
    for source in sources:
        assert "generationRequestStorageKey" in source
        assert "localStorage.getItem(storageKey)" in source
        assert "crypto.randomUUID()" in source
        assert "idempotency_key:requestId" in source
        assert "localStorage.removeItem(storageKey)" in source
        assert source.index("localStorage.removeItem(storageKey)") > source.index("if(!d.draft)")

    assert sources[0] == sources[1]


def test_advertiser_jwt_requirement_is_deployable_and_documented():
    repo_root = Path(__file__).resolve().parents[1]
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "SUPABASE_JWT_SECRET=" in env_example
    assert "SUPABASE_JWT_SECRET" in readme
    assert "ownership checks fail closed" in readme


def test_deployments_fail_closed_until_idempotency_migration_is_acknowledged():
    repo_root = Path(__file__).resolve().parents[1]
    for relative in (".github/workflows/ci.yml", ".github/workflows/daily-release.yml"):
        workflow = (repo_root / relative).read_text(encoding="utf-8")
        deploy = workflow[workflow.index("deploy-fly:") :]
        assert "Require AdClaw idempotency migration" in deploy
        assert "ADCLAW_IDEMPOTENCY_MIGRATION_APPLIED" in deploy
        assert '!= "2026_07_31"' in deploy
        assert deploy.index("Require AdClaw idempotency migration") < deploy.index("flyctl deploy")

    agent_setup = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "2026_07_31_adclaw_idempotency.sql" in agent_setup
    assert "invoice_grant_ready" in agent_setup
    assert "paid_generation_ready" in agent_setup
    assert "Both values must be `true`" in agent_setup
