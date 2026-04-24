"""Configuration management for SemeClaw."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)
    fallbacks: list[LLMFallbackConfig] = Field(default_factory=list)

    @field_validator("api_base")
    @classmethod
    def api_base_must_be_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("api_base must be a valid URL")
        return v


class LLMFallbackConfig(BaseModel):
    """Fallback LLM endpoint configuration."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)

    @field_validator("api_base")
    @classmethod
    def api_base_must_be_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("api_base must be a valid URL")
        return v


class WebSearchConfig(BaseModel):
    """Web search provider configuration."""

    provider: str = "brave"
    api_key: str


class WebReadConfig(BaseModel):
    """Web reading provider configuration."""

    provider: str = "crawl4ai"


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""

    bot_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)


class ChannelsConfig(BaseModel):
    """Channels configuration."""

    enabled: bool = False
    telegram: TelegramConfig | None = None


class Config(BaseModel):
    """Main configuration for SemeClaw."""

    workspace: Path
    llm: LLMConfig
    default_agent: str
    agents_path: Path = Field(default=Path("agents"))
    skills_path: Path = Field(default=Path("skills"))
    history_path: Path = Field(default=Path(".history"))
    websearch: WebSearchConfig | None = None
    webread: WebReadConfig | None = None
    channels: ChannelsConfig | None = None
    supabase_url: str | None = None
    supabase_key: str | None = None

    @model_validator(mode="after")
    def resolve_paths(self) -> Config:
        """Resolve relative paths to absolute using workspace."""
        for field_name in (
            "agents_path",
            "skills_path",
            "history_path",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, self.workspace / path)
        return self

    @classmethod
    def load(cls, workspace_dir: Path) -> Config:
        """Load configuration from workspace directory."""
        config_data = cls._load_config(workspace_dir)
        config_data["workspace"] = workspace_dir
        return cls.model_validate(config_data)

    @classmethod
    def _load_config(cls, workspace_dir: Path) -> dict[str, Any]:
        """Load config from YAML files, merging user and runtime configs."""
        # Load base runtime config
        runtime_config: dict[str, Any] = {}

        # Load user config and merge
        user_config_file = workspace_dir / "config.user.yaml"
        base_config_file = workspace_dir / "config.yaml"

        selected_file: Path | None = None
        if user_config_file.exists():
            selected_file = user_config_file
        elif base_config_file.exists():
            selected_file = base_config_file

        if selected_file is None:
            raise FileNotFoundError(f"Config file not found: {user_config_file} or {base_config_file}")

        with open(selected_file) as f:
            user_config = yaml.safe_load(f) or {}
        runtime_config = cls._deep_merge(runtime_config, user_config)

        return runtime_config

    def reload(self) -> None:
        """Reload configuration from disk and update in-place."""
        workspace = self.workspace
        config_data = self._load_config(workspace)
        config_data["workspace"] = workspace

        # Validate the new config
        new_config = self.model_validate(config_data)

        # Update all fields in place
        for field_name in self.model_fields:
            setattr(self, field_name, getattr(new_config, field_name))

        logger.info("Configuration reloaded successfully")

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep merge override dict into base dict.

        Args:
            base: The base configuration dictionary.
            override: The override configuration dictionary.

        Returns:
            Merged configuration dictionary.
        """
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value

        return result
