"""Notion task adapter.

Ingests rows from a Notion database where a configurable select property
equals the configured ingest value. Writes back by setting the same
property to a "done" sentinel and appending a toggle block containing the
orchestrator's rationale.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger("semeclaw.v1.notion")

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


@dataclass(frozen=True)
class NotionConfig:
    token: str
    database_id: str
    select_property: str
    ingest_value: str
    done_value: str
    tenant_id: str

    @classmethod
    def from_env(cls) -> NotionConfig | None:
        token = _env("NOTION_TOKEN")
        db = _env("NOTION_DATABASE_ID")
        if not token or not db:
            return None
        return cls(
            token=token,
            database_id=db,
            select_property=_env("NOTION_SELECT_PROPERTY") or "SemeClaw",
            ingest_value=_env("NOTION_INGEST_VALUE") or "queued",
            done_value=_env("NOTION_DONE_VALUE") or "done",
            tenant_id=_env("SEMECLAW_TENANT_ID") or "default",
        )


def probe() -> dict:
    cfg = NotionConfig.from_env()
    return {
        "id": "notion",
        "ok": cfg is not None,
        "required_env": ["NOTION_TOKEN", "NOTION_DATABASE_ID"],
        "optional_env": ["NOTION_SELECT_PROPERTY", "NOTION_INGEST_VALUE", "NOTION_DONE_VALUE"],
        "missing": [k for k in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not _env(k)],
        "remediation": (
            "Create an internal integration at https://www.notion.so/my-integrations, copy its "
            "token to NOTION_TOKEN, share the target database with the integration, then set "
            "NOTION_DATABASE_ID. A select property (default name 'SemeClaw') with values "
            "matching NOTION_INGEST_VALUE/NOTION_DONE_VALUE controls the ingest queue."
        ),
    }


def _headers(cfg: NotionConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Notion-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _plain_text(rich: list[dict] | None) -> str:
    return "".join((part.get("plain_text") or "") for part in (rich or []))


def _page_to_task(cfg: NotionConfig, page: dict) -> dict | None:
    props = page.get("properties") or {}
    title = ""
    description = ""
    for value in props.values():
        if not isinstance(value, dict):
            continue
        if value.get("type") == "title":
            title = _plain_text(value.get("title"))
        elif value.get("type") == "rich_text" and not description:
            description = _plain_text(value.get("rich_text"))
    if not title:
        return None
    return {
        "source": "notion",
        "source_id": page["id"],
        "tenant_id": cfg.tenant_id,
        "title": title.strip()[:120],
        "description": description.strip(),
        "status": "open",
        "assigned_agents": [],
        "meta": {
            "notion_page_id": page["id"],
            "notion_url": page.get("url"),
            "select_property": cfg.select_property,
        },
    }


async def ingest(limit: int = 25) -> AsyncIterator[dict]:
    cfg = NotionConfig.from_env()
    if cfg is None:
        return
    body = {
        "page_size": max(1, min(int(limit), 100)),
        "filter": {
            "property": cfg.select_property,
            "select": {"equals": cfg.ingest_value},
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.post(
                f"{API_BASE}/databases/{cfg.database_id}/query",
                headers=_headers(cfg),
                json=body,
            )
        except httpx.HTTPError as exc:
            logger.warning("notion ingest transport error: %s", exc)
            return
        if r.status_code >= 400:
            logger.warning("notion ingest HTTP %s: %s", r.status_code, r.text[:200])
            return
        try:
            payload = r.json()
        except ValueError:
            logger.warning("notion ingest non-JSON response")
            return
        for page in payload.get("results") or []:
            task = _page_to_task(cfg, page)
            if task is not None:
                yield task


async def writeback(task: dict, decision: dict) -> dict:
    cfg = NotionConfig.from_env()
    if cfg is None:
        return {"ok": False, "error": "notion not configured"}

    page_id = task.get("source_id")
    if not page_id:
        return {"ok": False, "error": "task missing source_id"}
    rationale = (decision.get("rationale") or "").strip()
    status = ((decision.get("task_patch") or {}).get("status")) or task.get("status")

    body_update = {
        "properties": {
            cfg.select_property: {"select": {"name": cfg.done_value}},
        }
    }
    body_block = {
        "children": [
            {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": f"SemeClaw decision · status -> {status}"}}],
                    "children": [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "text": {"content": rationale[:1900] or "(no rationale)"}}
                                ]
                            },
                        }
                    ],
                },
            }
        ]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r1 = await client.patch(f"{API_BASE}/pages/{page_id}", headers=_headers(cfg), json=body_update)
            r2 = await client.patch(f"{API_BASE}/blocks/{page_id}/children", headers=_headers(cfg), json=body_block)
        except httpx.HTTPError as exc:
            logger.warning("notion writeback transport error: %s", exc)
            return {"ok": False, "error": f"transport: {exc}"}
        ok = r1.status_code < 400 and r2.status_code < 400
        return {"ok": ok, "status_update": r1.status_code, "status_block": r2.status_code}
