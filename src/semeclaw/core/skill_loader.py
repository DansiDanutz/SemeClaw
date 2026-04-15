"""Skill definition loader for SemeClaw."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from semeclaw.utils.def_loader import parse_definition, discover_definitions


class SkillDef(BaseModel):
    """Skill definition."""

    id: str
    name: str
    description: str = ""
    content: str

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "id": "example_skill",
                "name": "Example Skill",
                "description": "An example skill",
                "content": "This is the skill content...",
            }
        }


class SkillLoader:
    """Loads skill definitions from SKILL.md files."""

    def __init__(self, skills_path: Path) -> None:
        """Initialize skill loader.

        Args:
            skills_path: Path to skills directory
        """
        self.skills_path = skills_path

    def discover_definitions(self) -> list[SkillDef]:
        """Discover all skill definitions from skills directory.

        Returns:
            List of SkillDef instances
        """
        return discover_definitions(
            self.skills_path,
            "SKILL.md",
            self._parse_skill_def,
        )

    def get(self, skill_id: str) -> SkillDef | None:
        """Get a skill by ID.

        Args:
            skill_id: Skill ID

        Returns:
            SkillDef or None if not found
        """
        skill_file = self.skills_path / skill_id / "SKILL.md"
        if not skill_file.exists():
            return None

        try:
            content = skill_file.read_text()
            return parse_definition(content, skill_id, self._parse_skill_def)
        except Exception:
            return None

    @staticmethod
    def _parse_skill_def(
        skill_id: str, frontmatter: dict[str, Any], body: str
    ) -> SkillDef:
        """Parse skill definition from frontmatter and body.

        Args:
            skill_id: Skill ID
            frontmatter: Parsed YAML frontmatter
            body: Markdown body content

        Returns:
            SkillDef instance
        """
        return SkillDef(
            id=skill_id,
            name=frontmatter.get("name", skill_id),
            description=frontmatter.get("description", ""),
            content=body.strip(),
        )
