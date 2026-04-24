"""
Multica adapter — bidirectional sync with the Multica agent platform.

Multica (https://github.com/multica-ai/multica) is a Go backend (Chi router)
+ PostgreSQL + Next.js frontend. The local daemon auto-detects agent CLIs on
PATH and exposes them as runtimes.

Real API endpoints (public, workspace-scoped):
  GET  /health                         → {status: "ok"}
  GET  /api/workspaces                 → [{id, name}]
  GET  /api/agents?workspace_id=...    → [{id, name, status, runtime_mode, ...}]
  GET  /api/agents/{id}/tasks          → [{id, title, status, ...}]
  GET  /api/issues?workspace_id=...    → {issues: [...], total: N}
  POST /api/issues                     → create issue
  PUT  /api/issues/{id}                → update issue
  POST /api/issues/{id}/comments       → add comment

Auth: Bearer token via Authorization header.
Workspace: X-Workspace-ID header (auto-discovered if not set).

When Multica is not running, the adapter falls back to reading/writing
local JSON state so the War Room never breaks.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.multica")

DEFAULT_MULTICA_URL = os.environ.get("MULTICA_SERVER_URL", "http://127.0.0.1:8080")
MULTICA_STATE_DIR = Path.home() / ".multica"

# Issue statuses used by Multica
VALID_STATUSES = ["backlog", "todo", "in_progress", "in_review", "done", "blocked", "cancelled"]


def _normalize_server_url(raw: str) -> str:
    """Convert ws/wss URLs to http/https and strip /ws suffix."""
    raw = raw.strip()
    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    netloc = parsed.netloc
    path = parsed.path
    if path == "/ws":
        path = ""
    return urllib.parse.urlunparse((scheme, netloc, path, "", "", "")).rstrip("/")


class MulticaAdapter(Adapter):
    """First-class Multica adapter with real HTTP mode + persistent local fallback."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        resolved = _normalize_server_url(base_url or DEFAULT_MULTICA_URL)
        super().__init__("multica", resolved)
        self.token = token or os.environ.get("MULTICA_API_TOKEN", "")
        self.workspace_id = workspace_id or os.environ.get("MULTICA_WORKSPACE_ID", "")
        self._client: httpx.AsyncClient | None = None
        self._state_file = MULTICA_STATE_DIR / "war_room_sync.json"
        self._load_local_state()

    # ------------------------------------------------------------------ #
    #  Local state
    # ------------------------------------------------------------------ #

    def _load_local_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._local_agents = data.get("agents", [])
                self._local_issues = data.get("issues", data.get("tasks", []))
            except Exception:
                self._local_agents = []
                self._local_issues = []
        else:
            self._local_agents = []
            self._local_issues = []

    @property
    def _local_tasks(self) -> list[dict[str, Any]]:
        """Back-compat alias for code that references _local_tasks."""
        return self._local_issues

    @_local_tasks.setter
    def _local_tasks(self, value: list[dict[str, Any]]) -> None:
        self._local_issues = value

    def _save_local_state(self) -> None:
        try:
            MULTICA_STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {"agents": self._local_agents, "issues": self._local_issues},
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save Multica local state: %s", e)

    # ------------------------------------------------------------------ #
    #  Connection
    # ------------------------------------------------------------------ #

    async def _ensure_client(self) -> httpx.AsyncClient | None:
        if self._client is None:
            headers: dict[str, str] = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            if self.workspace_id:
                headers["X-Workspace-ID"] = self.workspace_id
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0, headers=headers)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _is_reachable(self) -> bool:
        """Synchronous health probe against the configured base URL."""
        try:
            parsed = urllib.parse.urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            return False

    async def _ensure_workspace(self) -> bool:
        """Auto-discover workspace ID if not already set."""
        if self.workspace_id:
            return True
        client = await self._ensure_client()
        if client is None:
            return False
        try:
            r = await client.get("/api/workspaces")
            r.raise_for_status()
            workspaces = r.json()
            if workspaces:
                ws = workspaces[0]
                self.workspace_id = ws.get("id", "")
                # Update client headers with discovered workspace
                if self._client and self.workspace_id:
                    self._client.headers["X-Workspace-ID"] = self.workspace_id
                return True
        except Exception as e:
            logger.debug("Workspace auto-discovery failed: %s", e)
        return False

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        if not self._is_reachable():
            return AdapterHealth(
                name="multica",
                reachable=False,
                error=f"Multica not reachable at {self.base_url}",
                meta={"local_state": str(self._state_file)} if self._local_agents else None,
            )
        client = await self._ensure_client()
        if client is None:
            return AdapterHealth(name="multica", reachable=False, error="no client")
        try:
            r = await client.get("/health")
            r.raise_for_status()
            data = r.json()
            return AdapterHealth(
                name="multica",
                reachable=True,
                version=data.get("version"),
                meta={"status": data.get("status"), "base_url": self.base_url},
            )
        except Exception as e:
            return AdapterHealth(name="multica", reachable=False, error=str(e))

    # ------------------------------------------------------------------ #
    #  Read (sync TO war room)
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        agents = await self.list_agents()
        tasks: list[dict] = []
        for agent in agents:
            agent_tasks = await self.list_tasks(agent.get("id"))
            tasks.extend(agent_tasks)
        # Also pull unassigned issues when in real mode
        if self._is_reachable():
            all_issues = await self.list_tasks()
            seen = {t["id"] for t in tasks}
            for issue in all_issues:
                if issue["id"] not in seen:
                    tasks.append(issue)
        return [{"type": "agent", "data": a} for a in agents] + [{"type": "task", "data": t} for t in tasks]

    async def list_agents(self) -> list[dict[str, Any]]:
        if not self._is_reachable():
            return [self._normalize_agent(a) for a in self._local_agents]

        client = await self._ensure_client()
        if client is None:
            return [self._normalize_agent(a) for a in self._local_agents]

        try:
            await self._ensure_workspace()
            params: dict[str, Any] = {}
            if self.workspace_id:
                params["workspace_id"] = self.workspace_id
            r = await client.get("/api/agents", params=params)
            r.raise_for_status()
            return [self._normalize_agent(a) for a in r.json()]
        except Exception as e:
            logger.warning("Multica agents fetch failed (%s) — using local state", e)
            return [self._normalize_agent(a) for a in self._local_agents]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        if not self._is_reachable():
            issues = self._local_issues
            if agent_id:
                issues = [i for i in issues if i.get("assignee") == agent_id]
            return [self._normalize_task(i) for i in issues]

        client = await self._ensure_client()
        if client is None:
            return []

        try:
            if agent_id:
                r = await client.get(f"/api/agents/{agent_id}/tasks")
                r.raise_for_status()
                return [self._normalize_task(t) for t in r.json()]

            await self._ensure_workspace()
            params: dict[str, Any] = {}
            if self.workspace_id:
                params["workspace_id"] = self.workspace_id
            r = await client.get("/api/issues", params=params)
            r.raise_for_status()
            data = r.json()
            issues = data.get("issues", [])
            return [self._normalize_task(i) for i in issues]
        except Exception as e:
            logger.warning("Multica issues fetch failed: %s", e)
            issues = self._local_issues
            if agent_id:
                issues = [i for i in issues if i.get("assignee") == agent_id]
            return [self._normalize_task(i) for i in issues]

    # ------------------------------------------------------------------ #
    #  Write (sync FROM war room)
    # ------------------------------------------------------------------ #

    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        ok = True
        for change in changes:
            ctype = change.get("type")
            if ctype in ("create_issue", "create_task"):
                ok = ok and bool(await self.create_issue(**change.get("payload", {})))
            elif ctype in ("update_issue", "update_task"):
                ok = ok and await self.update_issue(
                    change.get("issue_id", change.get("task_id", "")),
                    change.get("updates", {}),
                )
            elif ctype in ("add_comment", "add_note"):
                ok = ok and await self.add_comment(
                    change.get("issue_id", change.get("task_id", "")),
                    change.get("content", change.get("comment", change.get("note", ""))),
                    change.get("author", "war_room"),
                )
            elif ctype == "move_issue":
                ok = ok and await self.update_issue(
                    change.get("issue_id", ""),
                    {"status": change.get("status", "")},
                )
        return ok

    async def create_issue(
        self,
        title: str,
        description: str = "",
        assignee: str | None = None,
        priority: str = "medium",
        status: str = "open",
        tags: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        issue = {
            "id": f"MC-{len(self._local_issues) + 1:04d}",
            "title": title,
            "description": description,
            "assignee": assignee,
            "priority": priority,
            "status": status,
            "tags": tags or [],
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "war_room",
        }

        if not self._is_reachable():
            self._local_issues.append(issue)
            self._save_local_state()
            logger.info("[LOCAL] Created Multica issue %s: %s", issue["id"], title)
            return issue

        client = await self._ensure_client()
        if client is None:
            return None

        try:
            await self._ensure_workspace()
            # Normalize status for the real Multica API
            api_status = status if status in VALID_STATUSES else "backlog"
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "status": api_status,
                "priority": priority,
            }
            if project_id:
                body["project_id"] = project_id
            r = await client.post("/api/issues", json=body)
            r.raise_for_status()
            created = r.json()
            logger.info("Created Multica issue %s: %s", created.get("id"), title)
            return created
        except Exception as e:
            logger.warning("Multica create_issue failed (%s) — saving locally", e)
            self._local_issues.append(issue)
            self._save_local_state()
            return issue

    # Back-compat alias used by old change payloads
    create_task = create_issue

    async def update_issue(self, issue_id: str, updates: dict[str, Any]) -> bool:
        if not self._is_reachable():
            for i in self._local_issues:
                if i["id"] == issue_id:
                    i.update(updates)
                    self._save_local_state()
                    logger.info("[LOCAL] Updated Multica issue %s", issue_id)
                    return True
            return False

        client = await self._ensure_client()
        if client is None:
            return False

        try:
            r = await client.put(f"/api/issues/{issue_id}", json=updates)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("Multica update_issue failed: %s", e)
            return False

    # Back-compat alias
    update_task = update_issue

    async def add_comment(self, issue_id: str, content: str, author: str = "war_room") -> bool:
        if not self._is_reachable():
            logger.info("[LOCAL] Comment on Multica issue %s: %s", issue_id, content[:60])
            return True

        client = await self._ensure_client()
        if client is None:
            return False

        try:
            r = await client.post(
                f"/api/issues/{issue_id}/comments",
                json={"content": content},
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("Multica add_comment failed: %s", e)
            return False

    # Back-compat alias
    add_note = add_comment

    # ------------------------------------------------------------------ #
    #  Normalize
    # ------------------------------------------------------------------ #

    def _normalize_agent(self, raw: dict[str, Any]) -> dict[str, Any]:
        role = raw.get("runtime_mode") or raw.get("description") or "Agent"
        archived = bool(raw.get("archived_at"))
        status = raw.get("status", "idle")
        if archived:
            status = "archived"
        return {
            "id": str(raw.get("id", "unknown")),
            "name": raw.get("name", "Unknown"),
            "role": role,
            "status": status,
            "platform": self.name,
            "visibility": raw.get("visibility"),
            "runtime_id": raw.get("runtime_id"),
            "max_concurrent_tasks": raw.get("max_concurrent_tasks"),
        }

    def _normalize_task(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map a Multica issue to the War Room task shape."""
        assignee = raw.get("assignee")
        if assignee is None:
            a_type = raw.get("assignee_type")
            a_id = raw.get("assignee_id")
            if a_type and a_id:
                assignee = f"{a_type}:{a_id}"
        return {
            "id": str(raw.get("id", "unknown")),
            "title": raw.get("title", "Untitled"),
            "status": raw.get("status", "backlog"),
            "assignee": assignee,
            "priority": raw.get("priority", "medium"),
            "description": raw.get("description", ""),
            "project_id": raw.get("project_id"),
            "identifier": raw.get("identifier"),
            "platform": self.name,
        }
