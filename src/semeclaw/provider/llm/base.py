"""Base LLM provider abstraction using litellm."""

from __future__ import annotations

from typing import Any, Optional, cast, TYPE_CHECKING

from litellm import acompletion, Choices
from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from semeclaw.utils.config import LLMConfig


class ToolCall:
    """Represents a tool call from the LLM."""

    def __init__(self, id: str, name: str, arguments: dict[str, Any]) -> None:
        """Initialize tool call."""
        self.id = id
        self.name = name
        self.arguments = arguments


class LLMProvider:
    """LLM provider using litellm for multi-provider support."""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        """Initialize LLM provider."""
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._settings = kwargs

    @classmethod
    def from_config(cls, config: "LLMConfig") -> "LLMProvider":
        """Create provider from LLMConfig."""
        return cls(
            model=config.model,
            api_key=config.api_key,
            api_base=config.api_base,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def chat(
        self,
        messages: list[Message],
        tool_schemas: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str, list[ToolCall] | None]:
        """Call LLM with messages and return response text and tool calls.

        Args:
            messages: List of chat messages
            tool_schemas: Optional list of tool schemas for function calling
            **kwargs: Additional arguments to pass to acompletion

        Returns:
            Tuple of (response_text, tool_calls or None)
        """
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
        }

        if self.api_base:
            request_kwargs["api_base"] = self.api_base

        if tool_schemas:
            request_kwargs["tools"] = tool_schemas

        request_kwargs.update(kwargs)

        response = await acompletion(**request_kwargs)
        message = cast(Choices, response.choices[0]).message

        # Parse tool calls if present
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                import json

                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        return message.content or "", tool_calls
