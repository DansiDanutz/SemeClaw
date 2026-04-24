"""Base tool abstraction and decorator for SemeClaw."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, get_origin


class BaseTool(ABC):
    """Abstract base class for tools."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        """Initialize a tool."""
        self.name = name
        self.description = description
        self.parameters = parameters

    @abstractmethod
    async def execute(self, args: dict[str, Any], session: Any) -> str:
        """Execute the tool with given arguments.

        Args:
            args: Tool arguments from the LLM
            session: AgentSession for context

        Returns:
            String result from tool execution
        """
        pass

    def to_llm_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(func: Callable[..., str]) -> BaseTool:
    """Decorator to convert a function into a BaseTool.

    The decorated function should have:
    - A docstring (used as description)
    - Type hints for parameters
    - Return type of str

    Example:
        @tool
        def read_file(path: str) -> str:
            '''Read a file and return its contents.'''
            return open(path).read()
    """
    sig = inspect.signature(func)
    doc = func.__doc__ or "No description available"
    description = doc.strip().split("\n")[0]

    # Build JSON schema from function signature
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("session",):
            continue

        annotation = param.annotation
        if annotation == inspect.Parameter.empty:
            annotation = str

        prop_type = _annotation_to_json_type(annotation)
        properties[param_name] = {"type": prop_type}

        # Check if parameter has a default value
        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    parameters = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    class FunctionTool(BaseTool):
        """Tool wrapping the decorated function."""

        async def execute(self, args: dict[str, Any], session: Any) -> str:
            result = func(**args)
            if inspect.iscoroutine(result):
                result = await result
            return result

    return FunctionTool(
        name=func.__name__,
        description=description,
        parameters=parameters,
    )


def _annotation_to_json_type(annotation: Any) -> str:
    """Convert Python type annotation to JSON schema type."""
    origin = get_origin(annotation)

    if annotation in (str, int, float, bool):
        type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
        return type_map[annotation]

    if origin in (list, tuple):
        return "array"

    if origin in (dict,):
        return "object"

    if annotation in (list, tuple):
        return "array"

    if annotation in (dict,):
        return "object"

    return "string"
