"""Base LLM provider abstraction using litellm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast, TYPE_CHECKING

from litellm import acompletion, Choices
from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from semeclaw.utils.config import LLMConfig, LLMFallbackConfig


@dataclass
class LLMEndpoint:
    """Concrete endpoint configuration used at runtime."""

    provider: str
    model: str
    api_key: str
    api_base: str | None
    temperature: float
    max_tokens: int


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
        self, endpoints: list[LLMEndpoint], **kwargs: Any
    ):
        """Initialize LLM provider."""
        self.endpoints = endpoints
        primary = endpoints[0]
        self.model = primary.model
        self.api_key = primary.api_key
        self.api_base = primary.api_base
        self.temperature = primary.temperature
        self.max_tokens = primary.max_tokens
        self._settings = kwargs

    @classmethod
    def from_config(cls, config: "LLMConfig") -> "LLMProvider":
        """Create provider from LLMConfig."""
        endpoints = [
            LLMEndpoint(
                provider=config.provider,
                model=config.model,
                api_key=config.api_key,
                api_base=config.api_base,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )
        ]
        for fallback in config.fallbacks:
            endpoints.append(cls._endpoint_from_fallback(config, fallback))

        return cls(endpoints=endpoints)

    @staticmethod
    def _endpoint_from_fallback(primary: "LLMConfig", fallback: "LLMFallbackConfig") -> LLMEndpoint:
        """Resolve fallback config with inherited defaults from the primary endpoint."""
        return LLMEndpoint(
            provider=fallback.provider,
            model=fallback.model,
            api_key=fallback.api_key,
            api_base=fallback.api_base,
            temperature=fallback.temperature if fallback.temperature is not None else primary.temperature,
            max_tokens=fallback.max_tokens if fallback.max_tokens is not None else primary.max_tokens,
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
        errors: list[str] = []
        last_exc: Exception | None = None

        for index, endpoint in enumerate(self.endpoints):
            request_kwargs: dict[str, Any] = {
                "model": endpoint.model,
                "messages": messages,
                "api_key": endpoint.api_key,
                "temperature": endpoint.temperature,
                "max_tokens": endpoint.max_tokens,
            }

            if endpoint.api_base:
                request_kwargs["api_base"] = endpoint.api_base

            if tool_schemas:
                request_kwargs["tools"] = tool_schemas

            request_kwargs.update(kwargs)

            try:
                response = await acompletion(**request_kwargs)
                message = cast(Choices, response.choices[0]).message

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

                self.model = endpoint.model
                self.api_key = endpoint.api_key
                self.api_base = endpoint.api_base
                self.temperature = endpoint.temperature
                self.max_tokens = endpoint.max_tokens
                return message.content or "", tool_calls
            except Exception as exc:
                last_exc = exc
                errors.append(f"{endpoint.model}: {exc}")
                has_more = index < len(self.endpoints) - 1
                if not has_more or not self._should_fallback(exc):
                    break

        if last_exc is None:
            raise RuntimeError("LLM call failed before a request was attempted")

        joined_errors = " | ".join(errors)
        raise RuntimeError(f"All LLM endpoints failed: {joined_errors}") from last_exc

    @staticmethod
    def _should_fallback(exc: Exception) -> bool:
        """Return True when a provider failure should trigger fallback."""
        message = str(exc).lower()
        fallback_terms = [
            "access denied",
            "adapter_failed",
            "api connection",
            "billing",
            "capacity",
            "connection",
            "credit",
            "exceeded your current quota",
            "insufficient_quota",
            "overdue-payment",
            "payment",
            "quota",
            "rate limit",
            "resource exhausted",
            "service unavailable",
            "temporarily unavailable",
            "timeout",
            "too many requests",
            "429",
            "502",
            "503",
            "504",
        ]
        return any(term in message for term in fallback_terms)
