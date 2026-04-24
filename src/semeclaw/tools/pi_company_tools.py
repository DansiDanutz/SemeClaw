"""Tools for managing pi-company integration from SemeClaw."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semeclaw.integrations.pi_company import (
    bootstrap_repo,
    format_bootstrap_result,
    format_status,
    get_status,
)
from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


def create_pi_company_tools(config: Config) -> list[BaseTool]:
    """Create pi-company integration tools."""

    class PiCompanyStatusTool(BaseTool):
        async def execute(self, args: dict[str, Any], session: Any) -> str:
            target_path = args.get("target_path") or config.workspace
            package_path = args.get("package_path")
            status = get_status(target_path=target_path, workspace=config.workspace, package_path=package_path)
            return format_status(status)

    class BootstrapPiCompanyTool(BaseTool):
        async def execute(self, args: dict[str, Any], session: Any) -> str:
            target_path = args.get("target_path")
            if not target_path:
                return "Error: target_path is required"

            company_name = args.get("company_name")
            package_path = args.get("package_path")
            force = bool(args.get("force", False))

            try:
                result = bootstrap_repo(
                    target_path=target_path,
                    workspace=config.workspace,
                    package_path=package_path,
                    company_name=company_name,
                    force=force,
                )
            except Exception as exc:
                return f"Error bootstrapping pi-company: {exc}"

            return format_bootstrap_result(result)

    return [
        PiCompanyStatusTool(
            name="pi_company_status",
            description="Inspect whether a repository is configured to use the pi-company package.",
            parameters={
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Path to the repository to inspect. Defaults to the SemeClaw workspace.",
                    },
                    "package_path": {
                        "type": "string",
                        "description": "Optional explicit path to the pi-company package.",
                    },
                },
            },
        ),
        BootstrapPiCompanyTool(
            name="bootstrap_pi_company",
            description=(
                "Set up a repository to use the pi-company package by writing "
                ".pi/settings.json and .pi/company/context.md."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Path to the repository that should use pi-company.",
                    },
                    "company_name": {
                        "type": "string",
                        "description": "Optional company name to render into the generated context file.",
                    },
                    "package_path": {
                        "type": "string",
                        "description": "Optional explicit path to the pi-company package.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Overwrite an existing .pi/company/context.md file.",
                    },
                },
                "required": ["target_path"],
            },
        ),
    ]
