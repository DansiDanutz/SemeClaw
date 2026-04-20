"""
War Room — Config helpers

Load LLM + Telegram config from the SemeClaw workspace. War Room previously
read `default_workspace/config.yaml` (wrong — the real file is
`config.user.yaml`) and looked up the Telegram token at `telegram.bot_token`
(the actual path under SemeClaw is `channels.telegram.bot_token`). This
module centralises the lookup so those bugs can't drift back in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("war_room.config")

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
_CONFIG_FILES = ("config.user.yaml", "config.yaml")


def _workspace_dir(root: Path) -> Path:
    return root / "default_workspace"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — config lookup skipped")
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug("Could not read %s: %s", path, e)
        return {}
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        logger.warning("Malformed YAML in %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def load_workspace_config(root: Path) -> dict[str, Any]:
    """Load the SemeClaw workspace config dict, trying config.user.yaml first."""
    ws = _workspace_dir(root)
    for name in _CONFIG_FILES:
        path = ws / name
        if path.exists():
            cfg = _load_yaml(path)
            if cfg:
                return cfg
    return {}


def resolve_model(root: Path, override: str | None = None) -> str:
    """Pick the LLM model string for litellm.

    Precedence: explicit override > config.user.yaml (`llm.model` or top-level
    `model`) > DEFAULT_MODEL. Prefixes `anthropic/` when the model looks like a
    bare Claude model and the provider is anthropic.
    """
    if override:
        return override
    cfg = load_workspace_config(root)
    llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    model = llm.get("model") or cfg.get("model") or DEFAULT_MODEL
    provider = (llm.get("provider") or "").lower()
    if provider == "anthropic" and "/" not in model:
        model = f"anthropic/{model}"
    return model


def resolve_api_key(root: Path) -> str | None:
    """Return the LLM API key from the workspace config, if present."""
    cfg = load_workspace_config(root)
    llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    return llm.get("api_key") or None


def resolve_telegram_token(root: Path) -> str | None:
    """Return the Telegram bot token from `channels.telegram.bot_token`.

    Also accepts the legacy `telegram.bot_token` path so older configs still
    work during the migration.
    """
    cfg = load_workspace_config(root)
    channels = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
    telegram = channels.get("telegram") if isinstance(channels.get("telegram"), dict) else {}
    token = telegram.get("bot_token")
    if token:
        return token
    # Legacy path
    legacy = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    return legacy.get("bot_token") or None
