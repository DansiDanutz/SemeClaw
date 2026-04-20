"""Manifest-backed plugin discovery for SemeClaw."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from semeclaw.tools.base import BaseTool
    from semeclaw.utils.config import Config


logger = logging.getLogger(__name__)


class PluginDef(BaseModel):
    """Definition of a SemeClaw plugin."""

    id: str
    name: str
    description: str = ""
    version: str = "0.1.0"
    tool_factory: str | None = None
    skill_paths: list[str] = Field(default_factory=list)


class PluginLoader:
    """Discover and load plugin manifests."""

    def __init__(self, roots: list[Path]):
        self.roots = [root.resolve() for root in roots]
        self._plugins: list[tuple[PluginDef, Path]] = []
        self._discover()

    @classmethod
    def from_workspace(cls, workspace: Path) -> "PluginLoader":
        repo_root = Path(__file__).resolve().parents[3]
        roots = [
            workspace / "integrations",
            repo_root / "integrations",
        ]
        return cls(roots)

    def _discover(self) -> None:
        seen: set[str] = set()
        for root in self.roots:
            if not root.exists():
                continue
            for manifest_path in sorted(root.glob("*/plugin.yaml")):
                try:
                    data = yaml.safe_load(manifest_path.read_text()) or {}
                    plugin = PluginDef(**data)
                except Exception as exc:
                    logger.warning(f"Failed to parse plugin manifest {manifest_path}: {exc}")
                    continue
                if plugin.id in seen:
                    continue
                seen.add(plugin.id)
                self._plugins.append((plugin, manifest_path.parent))

    def discover_plugins(self) -> list[PluginDef]:
        """Return discovered plugin definitions."""
        return [plugin for plugin, _ in self._plugins]

    def skill_paths(self) -> list[Path]:
        """Resolve all plugin-owned skill directories."""
        results: list[Path] = []
        seen: set[Path] = set()
        for plugin, plugin_dir in self._plugins:
            for rel_path in plugin.skill_paths:
                resolved = (plugin_dir / rel_path).resolve()
                if resolved in seen:
                    continue
                if resolved.exists():
                    results.append(resolved)
                    seen.add(resolved)
        return results

    def create_tools(self, config: "Config") -> list["BaseTool"]:
        """Instantiate tools from plugin factories."""
        tools: list["BaseTool"] = []
        for plugin, _plugin_dir in self._plugins:
            if not plugin.tool_factory:
                continue
            try:
                module_name, factory_name = plugin.tool_factory.split(":", 1)
                module = importlib.import_module(module_name)
                factory = getattr(module, factory_name)
                created = factory(config)
                if created is None:
                    continue
                if isinstance(created, list):
                    tools.extend(created)
                else:
                    tools.append(created)
            except Exception as exc:
                logger.warning(f"Failed to load tools for plugin {plugin.id}: {exc}")
        return tools
