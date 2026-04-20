"""
Paperclip adapter — bidirectional sync with the Paperclip Kanban/agent system.

Paperclip API (assumed RESTful):
  GET  /health                  → {status: "ok", version: "x.y.z"}
  GET  /board                   → {projects, columns, agents, issues}
  GET  /projects/{slug}/issues  → list[issue]
  GET  /agents/{name}/issues    → list[issue]
  POST /issues                  → create issue
  PATCH /issues/{id}            → update issue (status, assignee, etc)
  POST /issues/{id}/comments    → add comment

Mock mode is used when the API is unreachable so the War Room
never breaks — it just operates against local state.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.paperclip")

DEFAULT_BASE = "http://127.0.0.1:3100"

# Mapping from War Room agent ids to Paperclip assignee names
AGENT_MAP = {
    "research":   "Dexter",
    "architect":  "David",
    "strategist": "Memo",
    "writer":     "Hermes",
}

# Back-compat alias used by war_room.py
AGENT_ASSIGNEES = AGENT_MAP

PROJECTS = {
    "NERVIX":     "nervix",
    "CrawdBot":   "crawdbot",
    "MyWork AI":  "mywork-ai",
    "ZmartyChat": "zmartychat",
    "DansLab OS": "danslab-os",
}

LABEL_COLORS = {
    "research":           "#6366f1",
    "architecture":       "#f59e0b",
    "strategy":           "#10b981",
    "documentation":      "#3b82f6",
    "competitive-intel":  "#ef4444",
    "adr":                "#8b5cf6",
    "product-decision":   "#ec4899",
    "spec":               "#14b8a6",
    "roadmap":            "#f97316",
    "report":             "#64748b",
}


class PaperclipAdapter(Adapter):
    """First-class Paperclip adapter with bidirectional sync."""

    def __init__(self, base_url: str | None = None, mock: bool = False) -> None:
        super().__init__("paperclip", base_url or DEFAULT_BASE)
        self.mock = mock
        self._client: httpx.AsyncClient | None = None
        self._mock_issues: list[dict] = []
        self._issue_counter = 1000
        self._mock_path = Path("war_room/.paperclip_mock.json")
        self._load_mock()

    def _load_mock(self) -> None:
        if self._mock_path.exists():
            try:
                data = json.loads(self._mock_path.read_text(encoding="utf-8"))
                self._mock_issues = data.get("issues", [])
                self._issue_counter = data.get("counter", 1000)
            except Exception:
                pass

    def _save_mock(self) -> None:
        try:
            self._mock_path.parent.mkdir(parents=True, exist_ok=True)
            self._mock_path.write_text(
                json.dumps({"issues": self._mock_issues, "counter": self._issue_counter}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save Paperclip mock state: %s", e)

    # ------------------------------------------------------------------ #
    #  Connection
    # ------------------------------------------------------------------ #

    async def _ensure_client(self) -> httpx.AsyncClient | None:
        if self.mock:
            return None
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        if self.mock:
            return AdapterHealth(name="paperclip", reachable=False, error="running in mock mode")
        client = await self._ensure_client()
        if client is None:
            return AdapterHealth(name="paperclip", reachable=False, error="no client")
        try:
            r = await client.get("/health")
            r.raise_for_status()
            data = r.json()
            return AdapterHealth(
                name="paperclip",
                reachable=True,
                version=data.get("version"),
                meta={"status": data.get("status")},
            )
        except Exception as e:
            return AdapterHealth(name="paperclip", reachable=False, error=str(e))

    # ------------------------------------------------------------------ #
    #  Read (sync TO war room)
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        """Pull board state, agents, and issues from Paperclip."""
        agents = await self.list_agents()
        tasks: list[dict] = []
        for agent in agents:
            agent_tasks = await self.list_tasks(agent.get("name"))
            tasks.extend(agent_tasks)
        return [{"type": "agent", "data": a} for a in agents] + [{"type": "task", "data": t} for t in tasks]

    async def list_agents(self) -> list[dict[str, Any]]:
        board = await self._get_board_state()
        raw_agents = board.get("agents", {})
        return [self._normalize_agent({"id": k, **v}) for k, v in raw_agents.items()]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Return issues. If agent_id given, filter by assignee."""
        if agent_id:
            return await self._get_agent_workload(agent_id)
        board = await self._get_board_state()
        issues = board.get("issues", [])
        return [self._normalize_task(i) for i in issues]

    async def _get_board_state(self) -> dict[str, Any]:
        if self.mock:
            return self._mock_board_state()
        client = await self._ensure_client()
        if client is None:
            return self._mock_board_state()
        try:
            r = await client.get("/board")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Paperclip board fetch failed (%s) — falling back to mock", e)
            return self._mock_board_state()

    async def _get_agent_workload(self, agent_name: str) -> list[dict]:
        if self.mock:
            return [i for i in self._mock_issues if i.get("assignee") == agent_name and i.get("status") != "done"]
        client = await self._ensure_client()
        if client is None:
            return []
        try:
            r = await client.get(f"/agents/{agent_name}/issues")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Paperclip workload fetch failed: %s", e)
            return []

    # ------------------------------------------------------------------ #
    #  Write (sync FROM war room)
    # ------------------------------------------------------------------ #

    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        ok = True
        for change in changes:
            ctype = change.get("type")
            if ctype == "create_issue":
                ok = ok and bool(await self.create_issue(**change.get("payload", {})))
            elif ctype == "move_issue":
                ok = ok and await self.move_issue(
                    change.get("issue_id", ""), change.get("status", "")
                )
            elif ctype == "add_comment":
                ok = ok and await self.add_comment(
                    change.get("issue_id", ""),
                    change.get("comment", ""),
                    change.get("author", "war_room"),
                )
        return ok

    async def create_issue(
        self,
        title: str,
        description: str,
        project: str,
        assignee: str,
        labels: list[str] | None = None,
        priority: str = "medium",
        acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any] | None:
        issue = {
            "id": f"PC-{self._issue_counter}",
            "title": title,
            "description": description,
            "project": project,
            "assignee": assignee,
            "labels": labels or [],
            "priority": priority,
            "status": "backlog",
            "acceptance_criteria": acceptance_criteria or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "war_room",
        }

        if self.mock:
            self._issue_counter += 1
            self._mock_issues.append(issue)
            self._save_mock()
            logger.info("[MOCK] Created issue %s: %s → %s", issue["id"], title, assignee)
            return issue

        client = await self._ensure_client()
        if client is None:
            return None
        try:
            r = await client.post("/issues", json=issue)
            r.raise_for_status()
            created = r.json()
            logger.info("Created Paperclip issue %s: %s", created.get("id"), title)
            return created
        except Exception as e:
            logger.warning("create_issue API failed (%s) — saving mock issue", e)
            self._issue_counter += 1
            self._mock_issues.append(issue)
            self._save_mock()
            return issue

    async def move_issue(self, issue_id: str, status: str) -> bool:
        if self.mock:
            for i in self._mock_issues:
                if i["id"] == issue_id:
                    i["status"] = status
                    self._save_mock()
                    logger.info("[MOCK] Moved %s → %s", issue_id, status)
                    return True
            return False

        client = await self._ensure_client()
        if client is None:
            return False
        try:
            r = await client.patch(f"/issues/{issue_id}", json={"status": status})
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("move_issue failed: %s", e)
            return False

    async def add_comment(self, issue_id: str, comment: str, author: str = "war_room") -> bool:
        payload = {"comment": comment, "author": author, "created_at": datetime.now(timezone.utc).isoformat()}
        if self.mock:
            logger.info("[MOCK] Comment on %s: %s", issue_id, comment[:60])
            return True

        client = await self._ensure_client()
        if client is None:
            return False
        try:
            r = await client.post(f"/issues/{issue_id}/comments", json=payload)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("add_comment failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def resolve_project(self, project_name: str) -> str:
        return PROJECTS.get(project_name, project_name.lower().replace(" ", "-"))

    def resolve_assignee(self, agent_id: str) -> str:
        return AGENT_MAP.get(agent_id, agent_id.capitalize())

    def _mock_board_state(self) -> dict[str, Any]:
        return {
            "mode": "mock",
            "projects": list(PROJECTS.keys()),
            "columns": ["backlog", "in-progress", "review", "done"],
            "agents": {
                "Dexter":  {"role": "Research",    "status": "idle", "open_issues": 0},
                "David":   {"role": "Architect",   "status": "idle", "open_issues": 0},
                "Memo":    {"role": "Strategist",  "status": "idle", "open_issues": 0},
                "Hermes":  {"role": "Writer",      "status": "idle", "open_issues": 0},
                "Sienna":  {"role": "Crypto",      "status": "idle", "open_issues": 0},
                "Nano":    {"role": "AgentCreator","status": "idle", "open_issues": 0},
            },
            "issues": self._mock_issues,
        }

    async def get_board_state(self) -> dict[str, Any]:
        """Return raw board dict {projects, columns, agents, issues, mode}."""
        if self.mock:
            return self._mock_board_state()
        client = await self._ensure_client()
        if client is None:
            return self._mock_board_state()
        try:
            r = await client.get("/board")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Paperclip board fetch failed (%s) — falling back to mock", e)
            return self._mock_board_state()

    def get_mock_issues(self) -> list[dict]:
        return list(self._mock_issues)
