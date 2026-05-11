"""Tests for SemeClaw v1.0 enhancement modules.

These tests target the pure-logic surface (tenants store, audit query,
DLQ replay, convergence scorer, citations) so they run with no network
and no Supabase. The HTTP integration is covered separately.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    """Point all v1 storage at a per-test temp dir."""
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    # Force re-import so V1_DATA_DIR resolves against the new env.
    import war_room.v1.storage as storage

    importlib.reload(storage)
    yield tmp_path


@pytest.fixture()
def tenants_module(v1_data_dir):
    import war_room.v1.tenants as tenants

    importlib.reload(tenants)
    return tenants


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_tenant_returns_plain_key_only_once(tenants_module):
    tenant, plain_key = await tenants_module.create_tenant("Acme", plan="pro")
    assert tenant.id.startswith("tnt_")
    assert tenant.plan == "pro"
    assert tenant.status == "active"
    assert plain_key.startswith("sck_")
    # Hash on disk, not the plain key.
    assert tenants_module._hash_key(plain_key) == tenant.api_key_hash


@pytest.mark.asyncio
async def test_lookup_by_api_key_round_trips(tenants_module):
    tenant, plain_key = await tenants_module.create_tenant("Acme")
    found = await tenants_module.get_tenant_by_api_key(plain_key)
    assert found is not None
    assert found.id == tenant.id
    assert await tenants_module.get_tenant_by_api_key("sck_wrongkey") is None


@pytest.mark.asyncio
async def test_soft_delete_marks_deleted(tenants_module):
    tenant, _ = await tenants_module.create_tenant("Acme")
    deleted = await tenants_module.soft_delete(tenant.id)
    assert deleted is not None
    assert deleted.status == "deleted"
    assert deleted.deleted_at is not None

    visible = await tenants_module.list_tenants(include_deleted=False)
    assert all(t.id != tenant.id for t in visible)
    all_rows = await tenants_module.list_tenants(include_deleted=True)
    assert any(t.id == tenant.id for t in all_rows)


@pytest.mark.asyncio
async def test_update_plan_validates_unknown_plans(tenants_module):
    tenant, _ = await tenants_module.create_tenant("Acme")
    with pytest.raises(ValueError):
        await tenants_module.update_plan(tenant.id, "platinum")  # type: ignore[arg-type]
    updated = await tenants_module.update_plan(tenant.id, "enterprise")
    assert updated is not None
    assert updated.plan == "enterprise"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_append_and_query(v1_data_dir):
    import war_room.v1.storage as storage

    importlib.reload(storage)
    await storage.append_audit({"ts": "2026-05-11T10:00:00+00:00", "tenant_id": "acme",
                                "route": "/api/tenants", "method": "POST", "status": 201})
    await storage.append_audit({"ts": "2026-05-11T10:01:00+00:00", "tenant_id": "default",
                                "route": "/api/admin/dlq/tasks/replay", "method": "POST", "status": 200})

    by_tenant = storage.query_audit(tenant_id="acme")
    assert len(by_tenant) == 1
    assert by_tenant[0]["route"] == "/api/tenants"

    by_prefix = storage.query_audit(route_prefix="/api/admin")
    assert len(by_prefix) == 1
    assert by_prefix[0]["status"] == 200

    by_method = storage.query_audit(method="POST")
    assert len(by_method) == 2


# ---------------------------------------------------------------------------
# DLQ replay
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replay_drops_after_max_attempts(v1_data_dir, tmp_path, monkeypatch):
    import war_room.v1.storage as storage
    import war_room.v1.dlq_replay as replay

    importlib.reload(storage)
    importlib.reload(replay)

    dlq_path = tmp_path / "dlq.jsonl"
    dlq_path.write_text(
        "\n".join(
            [
                json.dumps({"kind": "always_fail", "attempt": 0}),
                json.dumps({"kind": "always_fail", "attempt": replay.MAX_ATTEMPTS}),
                json.dumps({"kind": "no_handler", "attempt": 0}),
            ]
        )
        + "\n"
    )

    @replay.register("always_fail")
    async def _h(entry):  # noqa: ARG001
        return False

    counters = await replay.replay_dlq(dlq_path)
    # First entry: failed, attempt -> 1. Second entry: dropped (>= cap). Third: skipped.
    assert counters["dropped"] == 1
    assert counters["skipped"] == 1
    assert counters["failed"] == 1
    # File rewritten with kept entries only.
    remaining = [json.loads(l) for l in dlq_path.read_text().splitlines() if l.strip()]
    assert len(remaining) == 2  # the failed (re-queued) + the no-handler
    kept_kinds = sorted(e["kind"] for e in remaining)
    assert kept_kinds == ["always_fail", "no_handler"]


@pytest.mark.asyncio
async def test_replay_handler_success_removes_entry(v1_data_dir, tmp_path):
    import war_room.v1.storage as storage
    import war_room.v1.dlq_replay as replay

    importlib.reload(storage)
    importlib.reload(replay)

    dlq_path = tmp_path / "dlq.jsonl"
    dlq_path.write_text(json.dumps({"kind": "happy", "attempt": 0}) + "\n")

    @replay.register("happy")
    async def _h(entry):  # noqa: ARG001
        return True

    counters = await replay.replay_dlq(dlq_path)
    assert counters["replayed"] == 1
    assert counters["failed"] == 0
    # File should be removed since nothing kept.
    assert not dlq_path.exists()


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------
def test_convergence_holds_first_turn():
    from war_room.v1.convergence import score_convergence

    s = score_convergence(turn=1, new_comment="agreed", prior_comments=[], prior_replies=[])
    assert not s.early_commit
    assert s.reason == "first_turn_holds"


def test_convergence_objection_lowers_score():
    from war_room.v1.convergence import score_convergence

    s = score_convergence(
        turn=2,
        new_comment="no, this is wrong, stop the rollout",
        prior_comments=["proceed"],
        prior_replies=["sounds good to me"],
    )
    assert not s.early_commit
    assert s.reason == "objection_present"


def test_convergence_clear_agreement_triggers_early_commit():
    from war_room.v1.convergence import score_convergence

    s = score_convergence(
        turn=2,
        new_comment="agreed agreed lgtm proceed approved confirmed correct ship it",
        prior_comments=["proceed lgtm approved"],
        prior_replies=["sounds good ship the patch"],
    )
    assert s.early_commit
    assert s.reason == "agreement_threshold_met"
    assert 0.6 <= s.score <= 1.0


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------
def test_citations_extracts_inline_urls():
    from war_room.v1.citations import attach_citations, collect_evidence_ids

    lines = [
        {
            "agent_id": "research",
            "role": "Researcher",
            "text": "See https://example.com/paper and https://arxiv.org/abs/1234",
        },
        {"agent_id": "writer", "role": "Writer", "text": "I'll draft it cleanly."},
    ]
    enriched = attach_citations(lines, {"title": "demo"})
    research_line = enriched[0]
    assert "citations" in research_line
    sources = sorted(c["source"] for c in research_line["citations"])
    assert sources == ["arxiv.org", "example.com"]
    # Evidence ids are stable.
    ids = collect_evidence_ids(enriched)
    assert len(ids) == 2


def test_citations_synthetic_for_research_without_urls():
    from war_room.v1.citations import attach_citations

    lines = [{"agent_id": "research", "role": "Researcher", "text": "I'll find sources."}]
    enriched = attach_citations(lines, {"title": "scaling postgres"})
    cites = enriched[0].get("citations") or []
    assert len(cites) >= 1
    # Search URL should embed the task title.
    assert any("scaling+postgres" in c["url"] for c in cites)


def test_citations_no_op_for_non_citing_agents():
    from war_room.v1.citations import attach_citations

    lines = [{"agent_id": "coder", "role": "Coder", "text": "I'll scaffold it."}]
    enriched = attach_citations(lines, {"title": "demo"})
    assert "citations" not in enriched[0]


# ---------------------------------------------------------------------------
# Discord adapter
# ---------------------------------------------------------------------------
def test_discord_probe_reports_missing_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    from war_room.v1 import discord_adapter

    p = discord_adapter.probe()
    assert p["id"] == "discord"
    assert p["ok"] is False
    assert sorted(p["missing"]) == ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"]


def test_discord_message_to_task_filters_non_task_messages(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
    from war_room.v1 import discord_adapter

    cfg = discord_adapter.DiscordConfig.from_env()
    assert cfg is not None

    # Non-task message ignored.
    assert discord_adapter._message_to_task(cfg, {"id": "1", "content": "hello"}) is None
    # Task message is parsed.
    task = discord_adapter._message_to_task(
        cfg,
        {"id": "42", "content": "!task Investigate latency in EU\nMore detail here.", "author": {"username": "dan"}},
    )
    assert task is not None
    assert task["title"] == "Investigate latency in EU"
    assert task["source"] == "discord"
    assert task["meta"]["discord_message_id"] == "42"


def test_discord_message_to_task_handles_leading_whitespace(monkeypatch):
    """Regression: title slice used to leak into description when there was
    whitespace after the !task prefix or when the title was truncated."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
    from war_room.v1 import discord_adapter

    cfg = discord_adapter.DiscordConfig.from_env()
    assert cfg is not None

    task = discord_adapter._message_to_task(
        cfg,
        {"id": "7", "content": "!task   Title with leading spaces\nBody only."},
    )
    assert task is not None
    assert task["title"] == "Title with leading spaces"
    assert task["description"] == "Body only."


def test_discord_message_to_task_truncates_long_title_into_description(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
    from war_room.v1 import discord_adapter

    cfg = discord_adapter.DiscordConfig.from_env()
    assert cfg is not None

    long_title = "A" * 130
    task = discord_adapter._message_to_task(
        cfg,
        {"id": "8", "content": f"!task {long_title}\nbody"},
    )
    assert task is not None
    assert len(task["title"]) == 120
    # The 10 overflow chars belong in the description, not the title.
    assert task["description"].startswith("A" * 10)
    assert task["description"].endswith("body")


# ---------------------------------------------------------------------------
# Audit ordering across shards (regression)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_ordering_across_shards_is_newest_first(v1_data_dir):
    import war_room.v1.storage as storage

    importlib.reload(storage)

    # Older shard
    older = storage.AUDIT_DIR / "2026-05-10.jsonl"
    older.write_text(
        "\n".join(
            json.dumps({"ts": f"2026-05-10T0{i}:00:00+00:00", "route": "/x", "method": "POST"})
            for i in range(6)
        )
        + "\n"
    )
    # Newer shard
    newer = storage.AUDIT_DIR / "2026-05-11.jsonl"
    newer.write_text(
        "\n".join(
            json.dumps({"ts": f"2026-05-11T0{i}:00:00+00:00", "route": "/y", "method": "POST"})
            for i in range(3)
        )
        + "\n"
    )

    rows = storage.query_audit(limit=5)
    timestamps = [r["ts"] for r in rows]
    # The result must be strictly newest-first, regardless of which shard
    # any given entry came from.
    assert timestamps == sorted(timestamps, reverse=True)
    # The newest entry across all shards must be at the head.
    assert timestamps[0] == "2026-05-11T02:00:00+00:00"


# ---------------------------------------------------------------------------
# DLQ replay concurrency (regression)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replay_preserves_concurrent_appends(v1_data_dir, tmp_path):
    import asyncio

    import war_room.v1.storage as storage
    import war_room.v1.dlq_replay as replay

    importlib.reload(storage)
    importlib.reload(replay)

    dlq_path = tmp_path / "dlq.jsonl"
    dlq_path.write_text(json.dumps({"kind": "slow_ok", "attempt": 0}) + "\n")

    started = asyncio.Event()
    proceed = asyncio.Event()

    @replay.register("slow_ok")
    async def _h(entry):  # noqa: ARG001
        started.set()
        await proceed.wait()
        return True

    # Kick off the replay; once the handler is mid-flight, append a fresh
    # entry that landed during the replay window and must survive.
    task = asyncio.create_task(replay.replay_dlq(dlq_path))
    await started.wait()
    await storage.append_jsonl(dlq_path, {"kind": "fresh", "attempt": 0})
    proceed.set()
    counters = await task

    assert counters["replayed"] == 1
    assert counters["preserved"] == 1
    remaining = [json.loads(l) for l in dlq_path.read_text().splitlines() if l.strip()]
    kinds = [e["kind"] for e in remaining]
    assert "fresh" in kinds  # the concurrent append must not be lost
