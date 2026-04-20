from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.core.plugin_loader import PluginLoader
from semeclaw.core.skill_loader import SkillLoader


def test_plugin_loader_discovers_manifests_and_skill_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    plugin_root = tmp_path / "integrations"
    workspace.mkdir()
    plugin_dir = plugin_root / "demo"
    skill_dir = tmp_path / "plugin-skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo Skill\n"
    )
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "id: demo",
                "name: Demo",
                "description: Demo plugin",
                "version: 0.1.0",
                f"skill_paths:\n  - {skill_dir.parent}",
            ]
        )
    )

    loader = PluginLoader([workspace / "integrations", plugin_root])
    plugins = loader.discover_plugins()

    assert plugins[0].id == "demo"
    assert skill_dir.parent.resolve() in loader.skill_paths()


def test_skill_loader_includes_plugin_skill_paths(tmp_path: Path) -> None:
    base_skills = tmp_path / "skills"
    plugin_skills = tmp_path / "plugin-skills"
    (base_skills / "base-skill").mkdir(parents=True)
    (base_skills / "base-skill" / "SKILL.md").write_text(
        "---\nname: base-skill\ndescription: base\n---\n\n# Base\n"
    )
    (plugin_skills / "plugin-skill").mkdir(parents=True)
    (plugin_skills / "plugin-skill" / "SKILL.md").write_text(
        "---\nname: plugin-skill\ndescription: plugin\n---\n\n# Plugin\n"
    )

    loader = SkillLoader(base_skills, extra_skill_paths=[plugin_skills])
    skills = {skill.id for skill in loader.discover_definitions()}

    assert "base-skill" in skills
    assert "plugin-skill" in skills
