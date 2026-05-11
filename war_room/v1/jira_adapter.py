"""Jira Cloud task adapter.

Ingests issues via JQL search; writes back by transitioning the issue
status (when JIRA_TRANSITION_MAP_JSON maps task statuses to transition
ids) and appending the orchestrator's rationale as a comment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

logger = logging.getLogger("semeclaw.v1.jira")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    jql: str
    transition_map: dict[str, str]
    tenant_id: str

    @property
    def auth_header(self) -> str:
        creds = f"{self.email}:{self.api_token}".encode()
        return "Basic " + base64.b64encode(creds).decode()

    @classmethod
    def from_env(cls) -> "JiraConfig | None":
        base = _env("JIRA_BASE_URL")
        email = _env("JIRA_EMAIL")
        token = _env("JIRA_API_TOKEN")
        if not (base and email and token):
            return None
        tmap: dict[str, str] = {}
        raw = _env("JIRA_TRANSITION_MAP_JSON")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    tmap = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError:
                logger.warning("JIRA_TRANSITION_MAP_JSON is not valid JSON; ignoring")
        return cls(
            base_url=base.rstrip("/"),
            email=email,
            api_token=token,
            jql=_env("JIRA_JQL") or 'labels = "semeclaw" AND statusCategory != Done',
            transition_map=tmap,
            tenant_id=_env("SEMECLAW_TENANT_ID") or "default",
        )


def probe() -> dict:
    cfg = JiraConfig.from_env()
    return {
        "id": "jira",
        "ok": cfg is not None,
        "required_env": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"],
        "optional_env": ["JIRA_JQL", "JIRA_TRANSITION_MAP_JSON"],
        "missing": [k for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not _env(k)],
        "remediation": (
            "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens. "
            "Set JIRA_BASE_URL (e.g. https://acme.atlassian.net), JIRA_EMAIL, and JIRA_API_TOKEN. "
            "Optional: JIRA_JQL filters which issues SemeClaw ingests; "
            "JIRA_TRANSITION_MAP_JSON maps status->transitionId for the writeback transitions API."
        ),
    }


def _headers(cfg: JiraConfig) -> dict[str, str]:
    return {"Authorization": cfg.auth_header, "Accept": "application/json", "Content-Type": "application/json"}


def _issue_to_task(cfg: JiraConfig, issue: dict) -> dict:
    fields = issue.get("fields") or {}
    return {
        "source": "jira",
        "source_id": issue["key"],
        "tenant_id": cfg.tenant_id,
        "title": fields.get("summary", "")[:120],
        "description": (fields.get("description") or "") if isinstance(fields.get("description"), str) else "",
        "status": ((fields.get("status") or {}).get("name") or "open").lower(),
        "assigned_agents": [],
        "meta": {
            "jira_key": issue["key"],
            "jira_status_id": (fields.get("status") or {}).get("id"),
            "jira_status_name": (fields.get("status") or {}).get("name"),
        },
    }


async def ingest(limit: int = 25) -> AsyncIterator[dict]:
    cfg = JiraConfig.from_env()
    if cfg is None:
        return
    params = {
        "jql": cfg.jql,
        "maxResults": str(max(1, min(int(limit), 100))),
        "fields": "summary,description,status,labels",
    }
    async with httpx.AsyncClient(timeout=15.0, base_url=cfg.base_url) as client:
        try:
            r = await client.get("/rest/api/3/search", headers=_headers(cfg), params=params)
        except httpx.HTTPError as exc:
            logger.warning("jira ingest transport error: %s", exc)
            return
        if r.status_code >= 400:
            logger.warning("jira ingest HTTP %s: %s", r.status_code, r.text[:200])
            return
        try:
            payload = r.json()
        except ValueError:
            logger.warning("jira ingest non-JSON response")
            return
        for issue in payload.get("issues") or []:
            yield _issue_to_task(cfg, issue)


async def writeback(task: dict, decision: dict) -> dict:
    cfg = JiraConfig.from_env()
    if cfg is None:
        return {"ok": False, "error": "jira not configured"}
    key = task.get("source_id")
    if not key:
        return {"ok": False, "error": "task missing source_id"}

    patch = decision.get("task_patch") or {}
    rationale = (decision.get("rationale") or "").strip()
    transition_id = cfg.transition_map.get(patch.get("status", ""))

    async with httpx.AsyncClient(timeout=10.0, base_url=cfg.base_url) as client:
        outcomes: dict = {"transition": None, "comment": None}
        try:
            if transition_id:
                r1 = await client.post(
                    f"/rest/api/3/issue/{key}/transitions",
                    headers=_headers(cfg),
                    json={"transition": {"id": transition_id}},
                )
                outcomes["transition"] = r1.status_code
            r2 = await client.post(
                f"/rest/api/3/issue/{key}/comment",
                headers=_headers(cfg),
                json={
                    "body": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": f"SemeClaw decision · status → {patch.get('status', task.get('status'))}"},
                                ],
                            },
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": rationale[:1900] or "(no rationale)"}],
                            },
                        ],
                    }
                },
            )
            outcomes["comment"] = r2.status_code
        except httpx.HTTPError as exc:
            logger.warning("jira writeback transport error: %s", exc)
            return {"ok": False, "error": f"transport: {exc}", **outcomes}
        ok = (outcomes["transition"] is None or outcomes["transition"] < 400) and outcomes["comment"] is not None and outcomes["comment"] < 400
        return {"ok": ok, **outcomes}
