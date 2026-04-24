"""Skill tool for accessing available skills."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semeclaw.core.plugin_loader import PluginLoader
from semeclaw.core.skill_loader import SkillLoader
from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


def create_skill_tool(config: Config) -> BaseTool:
    """Create a skill tool that provides access to available skills.

    Args:
        config: SemeClaw configuration

    Returns:
        BaseTool instance for skill access
    """
    plugin_loader = PluginLoader.from_workspace(config.workspace)
    skill_loader = SkillLoader(config.skills_path, extra_skill_paths=plugin_loader.skill_paths())
    skills = skill_loader.discover_definitions()

    # Build enum of available skill IDs
    skill_ids = [skill.id for skill in skills]

    class SkillTool(BaseTool):
        """Tool for accessing skill content."""

        async def execute(self, args: dict[str, Any], session: Any) -> str:
            """Execute skill lookup.

            Args:
                args: Must contain 'skill_id' key
                session: AgentSession (unused)

            Returns:
                Skill content or error message
            """
            skill_id = args.get("skill_id")
            if not skill_id:
                return "Error: skill_id parameter is required"

            skill = skill_loader.get(skill_id)
            if not skill:
                return f"Error: Skill not found: {skill_id}"

            return skill.content

    # Build description with available skills
    skills_list = "\n".join([f"  - {skill.id}: {skill.name}" for skill in skills])
    description = f"""Get the content of a skill. Available skills:
{skills_list}"""

    # Build JSON schema with enum of skill IDs
    parameters = {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "enum": skill_ids,
                "description": "The ID of the skill to retrieve",
            }
        },
        "required": ["skill_id"],
    }

    tool = SkillTool(
        name="get_skill",
        description=description,
        parameters=parameters,
    )

    return tool
