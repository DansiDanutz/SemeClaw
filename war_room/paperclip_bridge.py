"""
War Room — Paperclip Bridge
Connects War Room to the Paperclip Kanban/agent system.

Real Paperclip API: http://127.0.0.1:3100
Mock mode: used when Paperclip is not reachable (dev / offline).

Paperclip board agents:
  Dexter  → Research
  David   → Architect (CTO)
  Memo    → Strategist (PM)
  Hermes  → Writer (Brain)
  Sienna  → Crypto
  Nano    → Agent Creator
"""

import json
import logging
import httpx
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("war_room.paperclip")

PAPERCLIP_BASE = "http://127.0.0.1:3100"

# Projects in Paperclip
PROJECTS = {
    "NERVIX":     "nervix",
    "CrawdBot":   "crawdbot",
    "MyWork AI":  "mywork-ai",
    "ZmartyChat": "zmartychat",
    "DansLab OS": "danslab-os",
}

# Agent → Paperclip assignee mapping
AGENT_ASSIGNEES = {
    "research":   "Dexter",
    "architect":  "David",
    "strategist": "Memo",
    "writer":     "Hermes",
}

# Label → color (mock colours for display)
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


class PaperclipBridge:
    """
    Reads from and writes to the Paperclip board.
    Falls back to mock mode if the API is unreachable.
    """

    def __init__(self, base_url: str = PAPERCLIP_BASE, mock: bool = False):
        self.base_url = base_url
        self.mock = mock
        self._client: Optional[httpx.AsyncClient] = None
        self._mock_issues: list[dict] = []
        self._issue_counter = 1000

    async def __aenter__(self):
        if self.mock:
            logger.info("🎭 Paperclip running in MOCK mode")
            return self
        # Try to connect; if it fails, go mock
        try:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
            r = await self._client.get("/health")
            r.raise_for_status()
            logger.info("✅ Paperclip API connected at %s", self.base_url)
        except Exception:
            logger.warning("⚠️  Paperclip API not reachable — running in MOCK mode")
            if self._client:
                await self._client.aclose()
                self._client = None
            self.mock = True
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    #  Board state                                                          #
    # ------------------------------------------------------------------ #

    async def get_board_state(self) -> dict:
        """Return current board: projects, columns, issues, agents."""
        if self.mock:
            return self._mock_board_state()
        try:
            r = await self._client.get("/board")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("Paperclip get_board_state failed: %s — falling back to mock", e)
            return self._mock_board_state()

    async def get_project_issues(self, project: str) -> list[dict]:
        """Return all issues for a project."""
        if self.mock:
            return [i for i in self._mock_issues if i.get("project") == project]
        try:
            r = await self._client.get(f"/projects/{project}/issues")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("get_project_issues failed: %s", e)
            return []

    async def get_agent_workload(self, agent_name: str) -> list[dict]:
        """Return open issues assigned to a Paperclip agent."""
        if self.mock:
            return [i for i in self._mock_issues if i.get("assignee") == agent_name and i.get("status") != "done"]
        try:
            r = await self._client.get(f"/agents/{agent_name}/issues")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("get_agent_workload failed: %s", e)
            return []

    # ------------------------------------------------------------------ #
    #  Issue creation                                                       #
    # ------------------------------------------------------------------ #

    async def create_issue(
        self,
        title: str,
        description: str,
        project: str,
        assignee: str,
        labels: list[str] | None = None,
        priority: str = "medium",
        acceptance_criteria: list[str] | None = None,
    ) -> dict:
        """
        Create a new issue on the Paperclip board.
        Returns the created issue dict (with id).
        """
        issue = {
            "id":          f"PC-{self._issue_counter}",
            "title":       title,
            "description": description,
            "project":     project,
            "assignee":    assignee,
            "labels":      labels or [],
            "priority":    priority,
            "status":      "backlog",
            "acceptance_criteria": acceptance_criteria or [],
            "created_at":  datetime.utcnow().isoformat(),
            "created_by":  "war_room",
        }

        if self.mock:
            self._issue_counter += 1
            self._mock_issues.append(issue)
            logger.info("🎭 [MOCK] Created issue %s: %s → %s", issue["id"], title, assignee)
            return issue

        try:
            r = await self._client.post("/issues", json=issue)
            r.raise_for_status()
            created = r.json()
            logger.info("✅ Created Paperclip issue %s: %s", created.get("id"), title)
            return created
        except Exception as e:
            # Fall through to mock on failure
            logger.warning("create_issue API failed (%s) — saving mock issue", e)
            self._issue_counter += 1
            self._mock_issues.append(issue)
            return issue

    # ------------------------------------------------------------------ #
    #  Issue updates                                                        #
    # ------------------------------------------------------------------ #

    async def move_issue(self, issue_id: str, status: str) -> bool:
        """Move issue to a new column (status)."""
        if self.mock:
            for i in self._mock_issues:
                if i["id"] == issue_id:
                    i["status"] = status
                    logger.info("🎭 [MOCK] Moved %s → %s", issue_id, status)
                    return True
            return False
        try:
            r = await self._client.patch(f"/issues/{issue_id}", json={"status": status})
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("move_issue failed: %s", e)
            return False

    async def add_comment(self, issue_id: str, comment: str, author: str = "war_room") -> bool:
        """Add a comment to an existing issue."""
        payload = {"comment": comment, "author": author, "created_at": datetime.utcnow().isoformat()}
        if self.mock:
            logger.info("🎭 [MOCK] Comment on %s: %s", issue_id, comment[:60])
            return True
        try:
            r = await self._client.post(f"/issues/{issue_id}/comments", json=payload)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("add_comment failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    def format_issue_markdown(self, issue: dict) -> str:
        """Format a Paperclip issue as Markdown (for War Room reports)."""
        lines = [
            f"### [{issue['id']}] {issue['title']}",
            f"**Project:** {issue.get('project', '?')} | **Assignee:** {issue.get('assignee', '?')} | **Priority:** {issue.get('priority', '?')}",
            f"**Status:** {issue.get('status', '?')} | **Labels:** {', '.join(issue.get('labels', []))}",
            "",
            issue.get("description", ""),
        ]
        ac = issue.get("acceptance_criteria", [])
        if ac:
            lines += ["", "**Acceptance Criteria:**"]
            for item in ac:
                lines.append(f"- [ ] {item}")
        return "\n".join(lines)

    def resolve_project(self, project_name: str) -> str:
        """Resolve friendly project name to slug."""
        return PROJECTS.get(project_name, project_name.lower().replace(" ", "-"))

    def resolve_assignee(self, agent_id: str) -> str:
        """Resolve War Room agent id to Paperclip assignee name."""
        return AGENT_ASSIGNEES.get(agent_id, agent_id.capitalize())

    # ------------------------------------------------------------------ #
    #  Mock data                                                            #
    # ------------------------------------------------------------------ #

    def _mock_board_state(self) -> dict:
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

    def get_mock_issues(self) -> list[dict]:
        """Return all mock issues (for dashboard display)."""
        return list(self._mock_issues)


# ------------------------------------------------------------------ #
#  Convenience: load bridge from config                                #
# ------------------------------------------------------------------ #

def load_bridge(workspace_path: Path | None = None) -> "PaperclipBridge":
    """
    Create a PaperclipBridge.
    Reads PAPERCLIP_URL from war_room/config.json if present.
    """
    config_path = (workspace_path or Path(".")) / "war_room" / "config.json"
    url = PAPERCLIP_BASE
    mock = False

    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            url = cfg.get("paperclip_url", url)
            mock = cfg.get("mock", mock)
        except Exception:
            pass

    return PaperclipBridge(base_url=url, mock=mock)
