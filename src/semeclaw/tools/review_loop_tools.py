"""Tools for installing recurring repo review loops."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semeclaw.integrations.review_loop import (
    DEFAULT_REVIEW_LOOP_SCHEDULE,
    format_review_loop_install,
    install_review_loop,
)
from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


def create_review_loop_tools(config: "Config") -> list[BaseTool]:
    """Create tools for review loop management."""

    class InstallReviewLoopTool(BaseTool):
        async def execute(self, args: dict[str, Any], session: Any) -> str:
            repo_path = args.get("repo_path")
            if not repo_path:
                return "Error: repo_path is required"

            schedule = args.get("schedule") or DEFAULT_REVIEW_LOOP_SCHEDULE
            agent = args.get("agent") or "seme"
            force = bool(args.get("force", False))

            try:
                result = install_review_loop(
                    workspace=config.workspace,
                    repo_path=repo_path,
                    schedule=schedule,
                    agent=agent,
                    force=force,
                )
            except Exception as exc:
                return f"Error installing review loop: {exc}"

            return format_review_loop_install(result)

    return [
        InstallReviewLoopTool(
            name="install_review_loop",
            description="Install a recurring SemeClaw cron that generates repo review bundles automatically.",
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Target repository path that should be reviewed on a schedule.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": f"Cron schedule. Defaults to {DEFAULT_REVIEW_LOOP_SCHEDULE}.",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent to dispatch the review loop to. Defaults to seme.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Overwrite an existing review loop cron file if present.",
                    },
                },
                "required": ["repo_path"],
            },
        )
    ]
