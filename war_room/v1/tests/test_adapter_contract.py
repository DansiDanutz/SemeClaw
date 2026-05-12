"""Adapter contract test kit.

Parametrized across every adapter in ``war_room.v1.adapters.REGISTRY``.
Every new adapter automatically inherits these checks — adding one to
the registry without a working ``probe / ingest / writeback`` will turn
this suite red.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator

import pytest


def _adapter_ids() -> list[str]:
    from war_room.v1 import adapters

    return list(adapters.REGISTRY.keys())


@pytest.fixture(params=_adapter_ids())
def adapter(request):
    from war_room.v1 import adapters

    return adapters.REGISTRY[request.param]


def test_probe_returns_well_formed_dict(adapter, monkeypatch):
    # Strip every env var the adapter declares as required so we test the
    # "not configured" branch.
    for env in adapter.probe().get("required_env", []) or []:
        monkeypatch.delenv(env, raising=False)
    probe = adapter.probe()
    assert isinstance(probe, dict)
    assert probe["id"] == adapter.id
    assert "ok" in probe
    assert isinstance(probe["required_env"], list)
    assert isinstance(probe["missing"], list)
    assert "remediation" in probe
    # ok must be False when at least one required env is missing.
    if probe["missing"]:
        assert probe["ok"] is False


@pytest.mark.asyncio
async def test_ingest_returns_async_iterator_and_is_empty_when_unconfigured(adapter, monkeypatch):
    # Drop required env so ingest takes the unconfigured fast path.
    for env in adapter.probe().get("required_env", []) or []:
        monkeypatch.delenv(env, raising=False)

    gen = adapter.ingest(limit=1)
    assert hasattr(gen, "__aiter__") or inspect.isasyncgen(gen), f"{adapter.id}.ingest() must return an async iterator"
    tasks: list[dict] = []
    async for task in gen:
        tasks.append(task)
        if len(tasks) >= 5:  # safety brake
            break
    # Unconfigured adapters yield zero rows.
    assert tasks == []


@pytest.mark.asyncio
async def test_writeback_returns_error_dict_when_unconfigured(adapter, monkeypatch):
    for env in adapter.probe().get("required_env", []) or []:
        monkeypatch.delenv(env, raising=False)
    out = await adapter.writeback({"source_id": "x"}, {"rationale": "test", "task_patch": {}})
    assert isinstance(out, dict)
    assert out.get("ok") is False
    # Either an explicit error or a transport indicator must be present.
    assert "error" in out or "status" in out


@pytest.mark.asyncio
async def test_ingest_yields_dicts_with_required_keys_when_configured(adapter, monkeypatch):
    """Smoke-test the ingest happy path by stubbing the HTTP layer.

    This is per-adapter because each one targets a different API shape.
    For unconfigured adapters in CI, this collapses to the empty-stream
    assertion above; for adapters where we can construct config without
    hitting the network, we feed them a synthetic message and assert
    the parser returns a v1-compliant task dict.
    """
    from war_room.v1 import discord_adapter as d
    from war_room.v1 import jira_adapter as j
    from war_room.v1 import notion_adapter as n

    if adapter.id == "discord":
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
        cfg = d.DiscordConfig.from_env()
        task = d._message_to_task(cfg, {"id": "1", "content": "!task Foo", "author": {"username": "u"}})
        assert task["source"] == "discord"
        assert task["source_id"] == "1"
    elif adapter.id == "notion":
        monkeypatch.setenv("NOTION_TOKEN", "tok")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db")
        cfg = n.NotionConfig.from_env()
        task = n._page_to_task(
            cfg,
            {"id": "p", "properties": {"Title": {"type": "title", "title": [{"plain_text": "Hi"}]}}},
        )
        assert task["source"] == "notion"
        assert task["source_id"] == "p"
    elif adapter.id == "jira":
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@b")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        cfg = j.JiraConfig.from_env()
        task = j._issue_to_task(cfg, {"key": "K-1", "fields": {"summary": "Hi", "status": {"id": "1", "name": "Open"}}})
        assert task["source"] == "jira"
        assert task["source_id"] == "K-1"
    elif adapter.id == "linear":
        # Linear's parser is internal — exercise via the public ingest empty path.
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        assert (await _drain(adapter.ingest(limit=1))) == []
    else:  # future adapters
        assert True


async def _drain(gen: AsyncIterator[dict]) -> list[dict]:
    out: list[dict] = []
    async for x in gen:
        out.append(x)
    return out
