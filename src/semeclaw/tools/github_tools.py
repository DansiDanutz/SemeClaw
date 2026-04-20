"""GitHub PR tools for SemeClaw."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semeclaw.integrations.github_pr import (
    format_local_review_bundle_result,
    format_pr_status,
    format_review_bundle_result,
    get_pr_status,
    write_review_bundle_or_local,
)
from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


def create_github_tools(config: "Config") -> list[BaseTool]:
    """Create gh-backed GitHub PR tools."""

    class GitHubPrStatusTool(BaseTool):
        async def execute(self, args: dict[str, Any], session: Any) -> str:
            repo_path = args.get("repo_path") or config.workspace
            pr_ref = args.get("pr_ref")
            try:
                status = get_pr_status(repo_path=repo_path, pr_ref=pr_ref)
            except Exception as exc:
                return f"Error fetching PR status: {exc}"
            return format_pr_status(status)

    class GitHubPrReviewBundleTool(BaseTool):
        async def execute(self, args: dict[str, Any], session: Any) -> str:
            repo_path = args.get("repo_path") or config.workspace
            pr_ref = args.get("pr_ref")
            try:
                bundle = write_review_bundle_or_local(repo_path=repo_path, pr_ref=pr_ref)
            except Exception as exc:
                return f"Error creating review bundle: {exc}"
            if hasattr(bundle, "status"):
                return format_review_bundle_result(bundle)
            return format_local_review_bundle_result(bundle)

    return [
        GitHubPrStatusTool(
            name="github_pr_status",
            description="Inspect a GitHub pull request in the current repo or a given repo using gh.",
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path. Defaults to the SemeClaw workspace.",
                    },
                    "pr_ref": {
                        "type": "string",
                        "description": "Optional PR number, URL, or branch. Defaults to the current branch PR.",
                    },
                },
            },
        ),
        GitHubPrReviewBundleTool(
            name="github_pr_review_bundle",
            description=(
                "Generate a local markdown review bundle for a PR under .pi/reviews/ "
                "so pi-company can run a grounded PR review workflow."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path where the PR exists.",
                    },
                    "pr_ref": {
                        "type": "string",
                        "description": "Optional PR number, URL, or branch. Defaults to the current branch PR.",
                    },
                },
            },
        ),
    ]
