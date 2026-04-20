"""Compatibility shim — PaperclipBridge migrated to PaperclipAdapter.

This module re-exports the legacy API so existing imports in war_room.py
and tests continue to work while the codebase transitions to adapters.
"""

from __future__ import annotations

from pathlib import Path

from war_room.adapters.paperclip import (
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
    """Legacy bridge wrapper around PaperclipAdapter."""

    def __init__(self, workspace_path: Path | None = None) -> None:
        self.adapter = PaperclipAdapter()
        self.workspace_path = workspace_path

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
        """Create a Paperclip issue via the adapter."""
        return await self.adapter.create_issue(
            title=title,
            description=description,
            project=project,
            assignee=assignee,
            labels=labels or [],
            priority=priority,
            acceptance_criteria=acceptance_criteria or [],
        )

    def list_agents(self) -> list[dict]:
        """Return mock agent list for compatibility."""
        return []


def load_bridge(workspace_path: Path | None = None) -> PaperclipBridge:
    """Load a PaperclipBridge instance (legacy API)."""
    return PaperclipBridge(workspace_path=workspace_path)
