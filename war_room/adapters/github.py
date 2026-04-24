"""
GitHub adapter — bidirectional sync with GitHub Issues.

API endpoints:
  GET  /repos/{owner}/{repo}/issues          → list issues
  POST /repos/{owner}/{repo}/issues          → create issue
  PATCH /repos/{owner}/{repo}/issues/{number} → update issue
  POST /repos/{owner}/{repo}/issues/{number}/comments → add comment
  GET  /repos/{owner}/{repo}/assignees       → list assignees

Auth: Bearer token via Authorization header.
Config: GITHUB_TOKEN (required), GITHUB_REPO (owner/repo).

When GitHub is not reachable, the adapter falls back to local JSON state.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.github")

DEFAULT_BASE = "https://api.github.com"
GITHUB_STATE_DIR = Path.home() / ".semeclaw"


def _parse_repo(repo: str | None) -> tuple[str, str]:
    """Parse owner/repo from string or env."""
    raw = repo or os.environ.get("GITHUB_REPO", "")
    if "/" in raw:
        owner, name = raw.split("/", 1)
        return owner.strip(), name.strip()
    owner = os.environ.get("GITHUB_OWNER", "")
    name = os.environ.get("GITHUB_REPO_NAME", raw)
    return owner, name


class GitHubAdapter(Adapter):
    """First-class GitHub adapter with real HTTP mode + persistent local fallback."""

    def __init__(
        self,
        repo: str | None = None,
        token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__("github", base_url or DEFAULT_BASE)
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.owner, self.repo_name = _parse_repo(repo)
        self._client: httpx.AsyncClient | None = None
        self._state_file = GITHUB_STATE_DIR / "github_sync.json"
        self._load_local_state()

    def _load_local_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._local_issues = data.get("issues", [])
                self._local_agents = data.get("agents", [])
            except Exception:
                self._local_issues = []
                self._local_agents = []
        else:
            self._local_issues = []
            self._local_agents = []

    def _save_local_state(self) -> None:
        try:
            GITHUB_STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {"issues": self._local_issues, "agents": self._local_agents},
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save GitHub local state: %s", e)

    async def _ensure_client(self) -> httpx.AsyncClient | None:
        if self._client is None:
            headers: dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0, headers=headers)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def _is_reachable(self) -> bool:
        try:
            host = "api.github.com"
            port = 443
            with socket.create_connection((host, port), timeout=3.0):
                return True
        except OSError:
            return False

    def _repo_ok(self) -> bool:
        return bool(self.owner and self.repo_name)

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        if not self._is_reachable():
            return AdapterHealth(
                name="github",
                reachable=False,
                error="GitHub API not reachable",
                meta={"local_state": str(self._state_file)} if self._local_issues else None,
            )
        if not self.token:
            return AdapterHealth(
                name="github",
                reachable=True,
                error="GITHUB_TOKEN not set",
            )
        if not self._repo_ok():
            return AdapterHealth(
                name="github",
                reachable=True,
                error="GITHUB_REPO not set (expected owner/repo)",
            )
        client = await self._ensure_client()
        if client is None:
            return AdapterHealth(name="github", reachable=False, error="no client")
        try:
            r = await client.get("/user")
            r.raise_for_status()
            data = r.json()
            return AdapterHealth(
                name="github",
                reachable=True,
                version=data.get("login"),
                meta={"repo": f"{self.owner}/{self.repo_name}"},
            )
        except Exception as e:
            return AdapterHealth(name="github", reachable=False, error=str(e))

    # ------------------------------------------------------------------ #
    #  Read (sync TO war room)
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        agents = await self.list_agents()
        tasks = await self.list_tasks()
        return [{"type": "agent", "data": a} for a in agents] + [{"type": "task", "data": t} for t in tasks]

    async def list_agents(self) -> list[dict[str, Any]]:
        if not self._is_reachable() or not self._repo_ok():
            return [self._normalize_agent(a) for a in self._local_agents]

        client = await self._ensure_client()
        if client is None:
            return [self._normalize_agent(a) for a in self._local_agents]

        try:
            r = await client.get(f"/repos/{self.owner}/{self.repo_name}/assignees")
            r.raise_for_status()
            users = r.json()
            return [self._normalize_agent(u) for u in users]
        except Exception as e:
            logger.warning("GitHub assignees fetch failed (%s) — using local state", e)
            return [self._normalize_agent(a) for a in self._local_agents]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        if not self._is_reachable() or not self._repo_ok():
            issues = self._local_issues
            if agent_id:
                issues = [i for i in issues if i.get("assignee") == agent_id]
            return [self._normalize_task(i) for i in issues]

        client = await self._ensure_client()
        if client is None:
            return []

        try:
            params: dict[str, Any] = {"state": "all", "per_page": 100}
            if agent_id:
                params["assignee"] = agent_id
            r = await client.get(f"/repos/{self.owner}/{self.repo_name}/issues", params=params)
            r.raise_for_status()
            issues = r.json()
            # Filter out pull requests (GitHub returns PRs as issues too)
            issues = [i for i in issues if "pull_request" not in i]
            return [self._normalize_task(i) for i in issues]
        except Exception as e:
            logger.warning("GitHub issues fetch failed: %s", e)
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
            elif ctype in ("update_issue", "update_task", "move_issue"):
                updates = change.get("updates", {})
                if ctype == "move_issue":
                    updates["state"] = change.get("status", "open")
                ok = ok and await self.update_issue(
                    change.get("issue_number", change.get("task_id", 0)),
                    updates,
                )
            elif ctype in ("add_comment", "add_note"):
                ok = ok and await self.add_comment(
                    change.get("issue_number", change.get("task_id", 0)),
                    change.get("content", change.get("comment", change.get("note", ""))),
                )
        return ok

    async def create_issue(
        self,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        assignee: str | None = None,
        milestone: int | None = None,
    ) -> dict[str, Any] | None:
        issue = {
            "id": f"GH-{len(self._local_issues) + 1:04d}",
            "number": len(self._local_issues) + 1,
            "title": title,
            "body": body,
            "state": "open",
            "labels": labels or [],
            "assignee": assignee,
            "milestone": milestone,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "platform": "github",
        }

        if not self._is_reachable() or not self._repo_ok():
            self._local_issues.append(issue)
            self._save_local_state()
            logger.info("[LOCAL] Created GitHub issue %s: %s", issue["id"], title)
            return issue

        client = await self._ensure_client()
        if client is None:
            return None

        try:
            payload: dict[str, Any] = {"title": title, "body": body}
            if labels:
                payload["labels"] = labels
            if assignee:
                payload["assignee"] = assignee
            if milestone:
                payload["milestone"] = milestone
            r = await client.post(f"/repos/{self.owner}/{self.repo_name}/issues", json=payload)
            r.raise_for_status()
            created = r.json()
            logger.info("Created GitHub issue #%s: %s", created.get("number"), title)
            return created
        except Exception as e:
            logger.warning("GitHub create_issue failed (%s) — saving locally", e)
            self._local_issues.append(issue)
            self._save_local_state()
            return issue

    async def update_issue(self, issue_number: int | str, updates: dict[str, Any]) -> bool:
        issue_number = int(issue_number) if issue_number else 0
        if not self._is_reachable() or not self._repo_ok():
            for i in self._local_issues:
                if i.get("number") == issue_number or i.get("id") == f"GH-{issue_number:04d}":
                    i.update(updates)
                    self._save_local_state()
                    logger.info("[LOCAL] Updated GitHub issue #%s", issue_number)
                    return True
            return False

        client = await self._ensure_client()
        if client is None:
            return False

        try:
            r = await client.patch(
                f"/repos/{self.owner}/{self.repo_name}/issues/{issue_number}",
                json=updates,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("GitHub update_issue failed: %s", e)
            return False

    async def add_comment(self, issue_number: int | str, body: str) -> bool:
        issue_number = int(issue_number) if issue_number else 0
        if not self._is_reachable() or not self._repo_ok():
            logger.info("[LOCAL] Comment on GitHub issue #%s: %s", issue_number, body[:60])
            return True

        client = await self._ensure_client()
        if client is None:
            return False

        try:
            r = await client.post(
                f"/repos/{self.owner}/{self.repo_name}/issues/{issue_number}/comments",
                json={"body": body},
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("GitHub add_comment failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Normalize
    # ------------------------------------------------------------------ #

    def _normalize_agent(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(raw.get("login", raw.get("id", "unknown"))),
            "name": raw.get("login", raw.get("name", "Unknown")),
            "role": raw.get("type", "User"),
            "status": "active",
            "platform": self.name,
            "avatar_url": raw.get("avatar_url"),
        }

    def _normalize_task(self, raw: dict[str, Any]) -> dict[str, Any]:
        assignee = raw.get("assignee")
        assignee_name = assignee.get("login") if isinstance(assignee, dict) else assignee
        labels = raw.get("labels", [])
        if isinstance(labels, list) and labels and isinstance(labels[0], dict):
            labels = [l.get("name", "") for l in labels]
        return {
            "id": str(raw.get("id", raw.get("number", "unknown"))),
            "title": raw.get("title", "Untitled"),
            "status": raw.get("state", "open"),
            "assignee": assignee_name,
            "priority": "medium",
            "description": raw.get("body", ""),
            "number": raw.get("number"),
            "labels": labels if isinstance(labels, list) else [],
            "platform": self.name,
        }
