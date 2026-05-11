"""Linear task adapter.

Ingests Linear issues that carry the ``semeclaw`` label and writes the
orchestrator's decision back as an issue comment + a workflow state
transition (when LINEAR_STATE_MAP_JSON is provided).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

logger = logging.getLogger("semeclaw.v1.linear")

API_URL = "https://api.linear.app/graphql"
SEARCH_QUERY = """
query SemeClawTasks($filter: IssueFilter, $first: Int!) {
  issues(filter: $filter, first: $first) {
    nodes {
      id
      identifier
      title
      description
      state { id name type }
      labels { nodes { name } }
      updatedAt
    }
  }
}
"""

WRITEBACK_MUTATION = """
mutation WritebackIssue($id: String!, $input: IssueUpdateInput!, $comment: CommentCreateInput!) {
  issueUpdate(id: $id, input: $input) { success }
  commentCreate(input: $comment) { success }
}
"""

WRITEBACK_COMMENT_ONLY = """
mutation Comment($comment: CommentCreateInput!) {
  commentCreate(input: $comment) { success }
}
"""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


@dataclass(frozen=True)
class LinearConfig:
    api_key: str
    label: str
    tenant_id: str
    state_map: dict[str, str]  # task_patch.status -> Linear workflow state id

    @classmethod
    def from_env(cls) -> "LinearConfig | None":
        key = _env("LINEAR_API_KEY")
        if not key:
            return None
        state_map: dict[str, str] = {}
        raw = _env("LINEAR_STATE_MAP_JSON")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state_map = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError:
                logger.warning("LINEAR_STATE_MAP_JSON is not valid JSON; ignoring")
        return cls(
            api_key=key,
            label=_env("LINEAR_LABEL") or "semeclaw",
            tenant_id=_env("SEMECLAW_TENANT_ID") or "default",
            state_map=state_map,
        )


def probe() -> dict:
    cfg = LinearConfig.from_env()
    return {
        "id": "linear",
        "ok": cfg is not None,
        "required_env": ["LINEAR_API_KEY"],
        "optional_env": ["LINEAR_LABEL", "LINEAR_STATE_MAP_JSON"],
        "missing": [k for k in ("LINEAR_API_KEY",) if not _env(k)],
        "remediation": (
            "Create a personal API key at https://linear.app/settings/api, then set "
            "LINEAR_API_KEY. Optionally set LINEAR_LABEL (default 'semeclaw') and "
            "LINEAR_STATE_MAP_JSON to map task statuses to Linear workflow state IDs."
        ),
    }


def _headers(cfg: LinearConfig) -> dict[str, str]:
    return {"Authorization": cfg.api_key, "Content-Type": "application/json"}


def _node_to_task(cfg: LinearConfig, node: dict) -> dict:
    return {
        "source": "linear",
        "source_id": node["id"],
        "tenant_id": cfg.tenant_id,
        "title": node.get("title", ""),
        "description": node.get("description") or "",
        "status": (node.get("state") or {}).get("type", "open"),
        "assigned_agents": [],
        "meta": {
            "linear_identifier": node.get("identifier"),
            "linear_state_id": (node.get("state") or {}).get("id"),
            "linear_state_name": (node.get("state") or {}).get("name"),
            "labels": [l.get("name") for l in (node.get("labels") or {}).get("nodes", [])],
        },
    }


async def ingest(limit: int = 25) -> AsyncIterator[dict]:
    cfg = LinearConfig.from_env()
    if cfg is None:
        return
    payload = {
        "query": SEARCH_QUERY,
        "variables": {
            "filter": {"labels": {"name": {"eq": cfg.label}}},
            "first": max(1, min(int(limit), 100)),
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.post(API_URL, headers=_headers(cfg), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("linear ingest transport error: %s", exc)
            return
        if r.status_code >= 400:
            logger.warning("linear ingest HTTP %s: %s", r.status_code, r.text[:200])
            return
        try:
            data = r.json()
        except ValueError:
            logger.warning("linear ingest non-JSON response")
            return
        for node in (((data.get("data") or {}).get("issues") or {}).get("nodes") or []):
            yield _node_to_task(cfg, node)


async def writeback(task: dict, decision: dict) -> dict:
    cfg = LinearConfig.from_env()
    if cfg is None:
        return {"ok": False, "error": "linear not configured"}

    issue_id = task.get("source_id")
    if not issue_id:
        return {"ok": False, "error": "task missing source_id"}

    patch = decision.get("task_patch") or {}
    rationale = (decision.get("rationale") or "").strip()
    body = f"**SemeClaw decision** — status `{patch.get('status', task.get('status'))}`\n\n{rationale}"

    next_state_id = cfg.state_map.get(patch.get("status", "")) if patch else None
    variables = {
        "id": issue_id,
        "input": {"stateId": next_state_id} if next_state_id else {},
        "comment": {"issueId": issue_id, "body": body[:8000]},
    }
    query = WRITEBACK_MUTATION if next_state_id else WRITEBACK_COMMENT_ONLY
    if not next_state_id:
        variables = {"comment": variables["comment"]}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(API_URL, headers=_headers(cfg), json={"query": query, "variables": variables})
        except httpx.HTTPError as exc:
            logger.warning("linear writeback transport error: %s", exc)
            return {"ok": False, "error": f"transport: {exc}"}
        ok = r.status_code < 400
        try:
            payload = r.json() if r.content else {}
        except ValueError:
            payload = {}
        return {"ok": ok, "status": r.status_code, "payload": payload}
