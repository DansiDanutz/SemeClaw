from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.provider.llm.base import LLMProvider
from semeclaw.utils.config import LLMConfig


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


def test_llm_falls_back_on_access_denied_error() -> None:
    config = LLMConfig.model_validate(
        {
            "provider": "dashscope",
            "model": "qwen-plus",
            "api_key": "primary-key",
            "temperature": 0.2,
            "max_tokens": 123,
            "fallbacks": [
                {
                    "provider": "openrouter",
                    "model": "openrouter/qwen/qwen3.6-plus:free",
                    "api_key": "backup-key",
                    "api_base": "https://openrouter.ai/api/v1",
                }
            ],
        }
    )
    provider = LLMProvider.from_config(config)

    with patch("semeclaw.provider.llm.base.acompletion") as mock_completion:
        mock_completion.side_effect = [
            RuntimeError(
                "Access denied, please make sure your account is in good standing. error-code#overdue-payment"
            ),
            _response("fallback ok"),
        ]

        text, tool_calls = asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))

    assert text == "fallback ok"
    assert tool_calls is None
    assert mock_completion.call_count == 2
    assert mock_completion.call_args_list[0].kwargs["model"] == "qwen-plus"
    assert mock_completion.call_args_list[1].kwargs["model"] == "openrouter/qwen/qwen3.6-plus:free"
    assert mock_completion.call_args_list[0].kwargs["temperature"] == 0.2
    assert mock_completion.call_args_list[1].kwargs["max_tokens"] == 123


def test_llm_does_not_fallback_on_non_provider_error() -> None:
    config = LLMConfig.model_validate(
        {
            "provider": "dashscope",
            "model": "qwen-plus",
            "api_key": "primary-key",
            "fallbacks": [
                {
                    "provider": "openrouter",
                    "model": "openrouter/qwen/qwen3.6-plus:free",
                    "api_key": "backup-key",
                    "api_base": "https://openrouter.ai/api/v1",
                }
            ],
        }
    )
    provider = LLMProvider.from_config(config)

    with patch("semeclaw.provider.llm.base.acompletion") as mock_completion:
        mock_completion.side_effect = RuntimeError("Invalid message payload format")

        try:
            asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected RuntimeError")

    assert "Invalid message payload format" in message
    assert mock_completion.call_count == 1
