"""Compatibility shim — PaperclipBridge migrated to PaperclipAdapter.

This module re-exports the legacy API so existing imports in war_room.py
and tests continue to work while the codebase transitions to adapters.
"""

from __future__ import annotations

from pathlib import Path

try:
    from war_room.adapters.paperclip import (
        AGENT_ASSIGNEES,
        PaperclipAdapter,
    )
except ImportError:
    from adapters.paperclip import (
        AGENT_ASSIGNEES,
        PaperclipAdapter,
    )

PROJECTS = {
    "NERVIX":     "nervix",
    "CrawdBot":   "crawdbot",
    "MyWork AI":  "mywork-ai",
    "ZmartyChat": "zmartychat",
    "DansLab OS": "danslab-os",
}


class PaperclipBridge:
    """Legacy bridge wrapper around PaperclipAdapter.
    
    Supports mock mode for tests.
    """

    _mock_counter = 1000

    def __init__(self, workspace_path: Path | None = None, mock: bool = False, base_url: str | None = None) -> None:
        self.workspace_path = workspace_path
        self.mock = mock
        self.base_url = base_url or "http://127.0.0.1:3100"
        self.adapter = PaperclipAdapter()
        self._mock_issues: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def create_issue(
        self,
        title: str,
        description: str = "",
        project: str = "nervix",
        assignee: str = "Hermes",
        labels: list[str] | None = None,
        priority: str = "medium",
        acceptance_criteria: list[str] | None = None,
    ) -> dict:
        """Create a Paperclip issue via the adapter or mock store."""
        if self.mock:
            PaperclipBridge._mock_counter += 1
            issue = {
                "id": f"PC-{PaperclipBridge._mock_counter}",
                "title": title,
                "description": description,
                "project": project,
                "assignee": assignee,
                "labels": labels or [],
                "priority": priority,
                "status": "backlog",
                "acceptance_criteria": acceptance_criteria or [],
            }
            self._mock_issues.append(issue)
            return issue
        return await self.adapter.create_issue(
            title=title,
            description=description,
            project=project,
            assignee=assignee,
            labels=labels or [],
            priority=priority,
            acceptance_criteria=acceptance_criteria or [],
        )

    async def get_board_state(self) -> dict:
        """Return mock board state for compatibility."""
        return {
            "mode": "mock",
            "projects": {k: {"id": v, "name": k} for k, v in PROJECTS.items()},
            "columns": ["backlog", "in-progress", "review", "done"],
            "agents": list(set(AGENT_ASSIGNEES.values())) + ["David", "Unknown"],
        }

    async def move_issue(self, issue_id: str, status: str) -> bool:
        """Move an issue to a new status in mock mode."""
        if self.mock:
            for issue in self._mock_issues:
                if issue["id"] == issue_id:
                    issue["status"] = status
                    return True
            return False
        return False

    async def get_agent_workload(self, assignee: str) -> list[dict]:
        """Return issues assigned to an agent."""
        return [i for i in self._mock_issues if i.get("assignee") == assignee]

    async def get_project_issues(self, project: str) -> list[dict]:
        """Return issues for a project."""
        return [i for i in self._mock_issues if i.get("project") == project]

    def get_mock_issues(self) -> list[dict]:
        """Return all mock issues."""
        return self._mock_issues

    def resolve_project(self, project: str) -> str:
        """Resolve project name to slug."""
        return PROJECTS.get(project, project.lower().replace(" ", "-"))

    def resolve_assignee(self, agent_id: str) -> str:
        """Resolve agent ID to assignee name."""
        return AGENT_ASSIGNEES.get(agent_id, "Unknown")

    def format_issue_markdown(self, issue: dict) -> str:
        """Format an issue as markdown."""
        lines = [
            f"# {issue.get('id', '???')} — {issue.get('title', 'Untitled')}",
            "",
            f"**Project:** {issue.get('project', 'N/A')}",
            f"**Assignee:** {issue.get('assignee', 'N/A')}",
            f"**Priority:** {issue.get('priority', 'N/A')}",
            f"**Status:** {issue.get('status', 'N/A')}",
            "",
            f"{issue.get('description', '')}",
            "",
        ]
        ac = issue.get("acceptance_criteria", [])
        if ac:
            lines.append("## Acceptance Criteria")
            for item in ac:
                lines.append(f"- [ ] {item}")
        labels = issue.get("labels", [])
        if labels:
            lines.append("")
            lines.append(f"**Labels:** {', '.join(labels)}")
        return "\n".join(lines)

    def list_agents(self) -> list[dict]:
        """Return mock agent list for compatibility."""
        return []


def load_bridge(workspace_path: Path | None = None) -> PaperclipBridge:
    """Load a PaperclipBridge instance (legacy API)."""
    return PaperclipBridge(workspace_path=workspace_path)
