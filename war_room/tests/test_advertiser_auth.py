"""Tests for the advertiser ownership auth helper.

Covers strict mode, audit mode, demo-id bypass, bearer extraction, and the
JWT sub==advertiser_id invariant.
"""

from __future__ import annotations

import importlib

import jwt
import pytest
from fastapi import HTTPException

SECRET = "test-secret-do-not-use-in-prod"


def _make_token(sub: str, *, audience: str = "authenticated", secret: str = SECRET) -> str:
    return jwt.encode({"sub": sub, "aud": audience}, secret, algorithm="HS256")


class _Req:
    """Minimal request with a .headers dict mirror."""

    def __init__(self, **headers: str) -> None:
        self.headers = {k.lower(): v for k, v in headers.items()}


@pytest.fixture
def deps(monkeypatch):
    """Reload deps.py with a fresh env so module-level flags re-init."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SEMECLAW_ADVERTISER_AUTH_STRICT", "1")
    monkeypatch.setenv("SEMECLAW_ADVERTISER_DEMO_ID", "demo")
    from war_room.dashboard.routes import deps as mod

    importlib.reload(mod)
    yield mod
    # Restore default after test
    monkeypatch.delenv("SEMECLAW_ADVERTISER_AUTH_STRICT", raising=False)
    importlib.reload(mod)


@pytest.mark.asyncio
async def test_valid_token_allowed(deps) -> None:
    req = _Req(authorization=f"Bearer {_make_token('adv-123')}")
    # Should not raise
    await deps.require_advertiser_owner(req, "adv-123")


@pytest.mark.asyncio
async def test_mismatched_sub_rejected(deps) -> None:
    req = _Req(authorization=f"Bearer {_make_token('other-user')}")
    with pytest.raises(HTTPException) as exc:
        await deps.require_advertiser_owner(req, "adv-123")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_bearer_rejected(deps) -> None:
    req = _Req()
    with pytest.raises(HTTPException) as exc:
        await deps.require_advertiser_owner(req, "adv-123")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_signature_rejected(deps) -> None:
    bogus = jwt.encode({"sub": "adv-123", "aud": "authenticated"}, "wrong-secret", algorithm="HS256")
    req = _Req(authorization=f"Bearer {bogus}")
    with pytest.raises(HTTPException) as exc:
        await deps.require_advertiser_owner(req, "adv-123")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_demo_id_bypasses_auth(deps) -> None:
    req = _Req()  # no bearer at all
    await deps.require_advertiser_owner(req, "demo")  # must not raise


@pytest.mark.asyncio
async def test_legacy_audit_mode_setting_still_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SEMECLAW_ADVERTISER_AUTH_STRICT", "0")
    from war_room.dashboard.routes import deps as mod

    importlib.reload(mod)
    req = _Req(authorization=f"Bearer {_make_token('other-user')}")
    with pytest.raises(HTTPException) as exc:
        await mod.require_advertiser_owner(req, "adv-123")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_without_secret_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SEMECLAW_ADVERTISER_AUTH_STRICT", "1")
    from war_room.dashboard.routes import deps as mod

    importlib.reload(mod)
    req = _Req()  # no bearer
    with pytest.raises(HTTPException) as exc:
        await mod.require_advertiser_owner(req, "adv-123")
    assert exc.value.status_code == 401
