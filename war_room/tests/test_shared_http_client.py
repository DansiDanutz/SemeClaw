"""Tests for the shared httpx client helper."""

from __future__ import annotations

import pytest

from war_room.utils.http_client import close_shared_clients, get_shared_client


@pytest.mark.asyncio
async def test_same_base_url_returns_same_client() -> None:
    a = await get_shared_client(base_url="https://example.invalid")
    b = await get_shared_client(base_url="https://example.invalid")
    assert a is b
    await close_shared_clients()


@pytest.mark.asyncio
async def test_different_base_urls_are_isolated() -> None:
    a = await get_shared_client(base_url="https://one.invalid")
    b = await get_shared_client(base_url="https://two.invalid")
    assert a is not b
    await close_shared_clients()


@pytest.mark.asyncio
async def test_recreates_after_close() -> None:
    a = await get_shared_client(base_url="https://recreate.invalid")
    await close_shared_clients()
    b = await get_shared_client(base_url="https://recreate.invalid")
    assert a is not b
    assert not b.is_closed
    await close_shared_clients()


@pytest.mark.asyncio
async def test_tasks_db_uses_shared_client(monkeypatch) -> None:
    """tasks._db._supa_once should delegate to get_shared_client.

    Test is self-contained: sets env, then patches the module\'s symbol
    directly without reloading. Avoids test-order dependencies.
    """
    monkeypatch.setenv("DLS_TEAM_SUPABASE_URL", "https://probe.invalid")
    monkeypatch.setenv("DLS_TEAM_SUPABASE_SERVICE_KEY", "k")
    from war_room.tasks import _db

    # Pin module-level constants without reloading
    monkeypatch.setattr(_db, "SUPA_URL", "https://probe.invalid")
    monkeypatch.setattr(_db, "SUPA_KEY", "k")
    monkeypatch.setattr(_db, "SUPA_HEADERS", {})

    called = {"n": 0}

    async def fake_get_shared_client(**kw):
        called["n"] += 1

        class _C:
            is_closed = False

            async def get(self, path, **kw):
                class R:
                    status_code = 200
                    content = b""

                    def json(self_inner):
                        return []

                return R()

        return _C()

    monkeypatch.setattr(_db, "get_shared_client", fake_get_shared_client)
    await _db._supa_once("get", "foo")
    assert called["n"] == 1
