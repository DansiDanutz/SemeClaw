"""Tool registry for managing available tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from semeclaw.tools.base import BaseTool


class ToolRegistry:
    """Registry for managing tools."""

    def __init__(self) -> None:
        """Initialize tool registry."""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool.

        Args:
            tool: BaseTool instance to register
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name.

        Args:
            name: Tool name

        Returns:
            BaseTool instance or None if not found
        """
        return self._tools.get(name)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get LLM-compatible tool schemas for all registered tools.

        Returns:
            List of OpenAI-compatible tool schemas
        """
        return [tool.to_llm_schema() for tool in self._tools.values()]

    def list_tools(self) -> list[str]:
        """Get list of registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())

    @classmethod
    def with_builtins(cls) -> ToolRegistry:
        """Create a new registry with built-in tools.

        Returns:
            ToolRegistry with built-in tools registered
        """
        from semeclaw.tools.builtin_tools import read_file, write_file, edit_file, bash

        registry = cls()
        registry.register(read_file)
        registry.register(write_file)
        registry.register(edit_file)
        registry.register(bash)
        return registry
