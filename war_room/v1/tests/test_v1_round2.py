"""Round-2 tests: spotlight, adapters, usage, SSE, exports.

No ``importlib.reload`` here — storage resolves paths from the env var
on every call, so just setting ``SEMECLAW_V1_DATA_DIR`` per-test keeps
state fully isolated.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# Spotlight
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_spotlight_loads_source_with_anchor(v1_data_dir):
    from war_room.v1 import spotlight as spot

    items = await spot.list_items()
    # Source-of-truth ships at least one anchor (Nervix.ai).
    assert len(items) >= 1
    assert any(i.pinned for i in items)


@pytest.mark.asyncio
async def test_spotlight_override_round_trip(v1_data_dir):
    from war_room.v1 import spotlight as spot

    item = await spot.add_override(
        {
            "id": "acme",
            "name": "Acme Robotics",
            "tagline": "Industrial automation for ops teams.",
            "description": "End-to-end robotics ops",
            "url": "https://acme.example",
            "logo_letter": "A",
            "gradient_from": "#222",
            "gradient_to": "#444",
            "accent_rgb": "55,55,55",
            "pinned": False,
            "featured_from": "2026-04-01T00:00:00Z",
        }
    )
    assert item.id == "acme"
    items = await spot.list_items(include_ineligible=True)
    assert any(i.id == "acme" for i in items)
    removed = await spot.remove_override("acme")
    assert removed is True
    items = await spot.list_items(include_ineligible=True)
    assert all(i.id != "acme" for i in items)


@pytest.mark.asyncio
async def test_pick_one_records_impression(v1_data_dir):
    from war_room.v1 import spotlight as spot

    item = await spot.pick_one()
    assert item is not None
    await spot.record_impression(item.id, source="test", tenant_id="acme")
    counts = spot.impression_counts()
    assert counts.get(item.id, 0) >= 1


# ---------------------------------------------------------------------------
# Adapters registry
# ---------------------------------------------------------------------------
def test_adapter_registry_lists_all(monkeypatch):
    for env in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CHANNEL_ID",
        "LINEAR_API_KEY",
        "NOTION_TOKEN",
        "NOTION_DATABASE_ID",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(env, raising=False)
    from war_room.v1 import adapters

    probes = adapters.all_probes()
    ids = sorted(p["id"] for p in probes)
    assert ids == ["discord", "jira", "linear", "notion"]
    assert all(p["ok"] is False for p in probes)


def test_linear_probe_reports_required_env(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    from war_room.v1 import linear_adapter

    p = linear_adapter.probe()
    assert p["id"] == "linear"
    assert p["missing"] == ["LINEAR_API_KEY"]


def test_notion_probe_reports_required_env(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    from war_room.v1 import notion_adapter

    p = notion_adapter.probe()
    assert sorted(p["missing"]) == ["NOTION_DATABASE_ID", "NOTION_TOKEN"]


def test_jira_probe_reports_required_env(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    from war_room.v1 import jira_adapter

    p = jira_adapter.probe()
    assert sorted(p["missing"]) == ["JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL"]


def test_notion_page_to_task_extracts_title(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db")
    from war_room.v1 import notion_adapter as na

    cfg = na.NotionConfig.from_env()
    assert cfg is not None
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Title": {"type": "title", "title": [{"plain_text": "Investigate latency"}]},
            "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "EU only"}]},
        },
    }
    task = na._page_to_task(cfg, page)
    assert task is not None
    assert task["title"] == "Investigate latency"
    assert task["description"] == "EU only"
    assert task["source"] == "notion"


def test_jira_issue_to_task_normalises_status(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "ops@acme")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    from war_room.v1 import jira_adapter as ja

    cfg = ja.JiraConfig.from_env()
    assert cfg is not None
    issue = {
        "key": "OPS-42",
        "fields": {"summary": "Latency probe", "description": "EU", "status": {"id": "5", "name": "In Progress"}},
    }
    task = ja._issue_to_task(cfg, issue)
    assert task["source_id"] == "OPS-42"
    assert task["status"] == "in progress"
    assert task["meta"]["jira_status_name"] == "In Progress"


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_usage_increment_and_rollup(v1_data_dir):
    from war_room.v1 import usage

    await usage.increment("acme", meetings=3, tts_chars=120)
    await usage.increment("acme", meetings=2, llm_tokens=4000)
    await usage.increment("globex", meetings=1)

    snap = await usage.snapshot("acme")
    assert snap["meetings"] == 5
    assert snap["tts_chars"] == 120
    assert snap["llm_tokens"] == 4000

    rows = await usage.rollup()
    by_tenant = {r["tenant_id"]: r for r in rows}
    assert by_tenant["acme"]["meetings"] == 5
    assert by_tenant["globex"]["meetings"] == 1


@pytest.mark.asyncio
async def test_quota_check_caps_free_plan(v1_data_dir):
    from war_room.v1 import tenants, usage

    tenant, _ = await tenants.create_tenant("Tiny", plan="free")
    # Free plan has 10 meetings/mo. Push it over.
    await usage.increment(tenant.id, meetings=11)
    check = await usage.check_quota(tenant.id, kind="meetings")
    assert check.allowed is False
    assert check.reason == "plan_cap_reached"
    assert check.used == 11


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_publish_delivers_to_subscribers():
    from war_room.v1 import sse

    stream = sse.stream()

    welcome = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert b"event: hello" in welcome

    sse.publish("audit", {"x": 1})
    second = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    text = second.decode("utf-8")
    assert "event: audit" in text
    assert '"x":1' in text

    await stream.aclose()
    assert sse.subscriber_count() == 0


# ---------------------------------------------------------------------------
# CSV exporters
# ---------------------------------------------------------------------------
def test_audit_csv_serialises_known_columns():
    from war_room.v1 import exports

    rows = [
        {
            "ts": "2026-05-11T10:00:00Z",
            "tenant_id": "acme",
            "method": "POST",
            "route": "/api/x",
            "status": 201,
            "latency_ms": 4,
        },
        {"ts": "2026-05-11T10:01:00Z", "tenant_id": "globex", "method": "DELETE", "route": "/api/y", "status": 401},
    ]
    csv = exports.audit_csv(rows)
    lines = csv.strip().split("\n")
    assert lines[0].startswith("ts,request_id,tenant_id,actor,method,route,status,latency_ms")
    assert "acme" in lines[1]
    assert "globex" in lines[2]


def test_usage_csv_includes_zero_columns():
    from war_room.v1 import exports

    csv = exports.usage_csv(
        [{"tenant_id": "acme", "period": "2026-05", "meetings": 3, "tts_chars": 0, "llm_tokens": 0, "interventions": 1}]
    )
    assert "tenant_id,period,meetings,tts_chars,llm_tokens,interventions,updated_at" in csv
    assert "acme,2026-05,3,0,0,1" in csv


# ---------------------------------------------------------------------------
# Audit retention
# ---------------------------------------------------------------------------
def test_audit_gc_drops_old_shards(v1_data_dir):
    from war_room.v1 import audit, storage

    audit_dir = storage._data_dir() / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    old = audit_dir / "1970-01-01.jsonl"
    fresh = audit_dir / "2999-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    fresh.write_text("{}\n", encoding="utf-8")
    removed = audit.gc_audit(keep_days=30)
    assert removed >= 1
    assert not old.exists()
    assert fresh.exists()
