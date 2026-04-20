"""Tests for war_room.config — model + telegram token resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

import config as wr_config


def _make_workspace(tmp_path: Path, data: dict) -> Path:
    ws = tmp_path / "default_workspace"
    ws.mkdir()
    (ws / "config.user.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def test_resolve_model_from_user_config(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    })
    assert wr_config.resolve_model(root) == "anthropic/claude-sonnet-4-20250514"


def test_resolve_model_preserves_explicit_prefix(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {
        "llm": {"provider": "anthropic", "model": "anthropic/claude-opus-4-6"},
    })
    assert wr_config.resolve_model(root) == "anthropic/claude-opus-4-6"


def test_resolve_model_override_wins(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    })
    assert wr_config.resolve_model(root, override="openai/gpt-4o") == "openai/gpt-4o"


def test_resolve_model_falls_back_to_default(tmp_path: Path) -> None:
    # No config at all
    assert wr_config.resolve_model(tmp_path) == wr_config.DEFAULT_MODEL


def test_resolve_telegram_token_channels_path(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {
        "channels": {"telegram": {"bot_token": "NEW-TOKEN"}},
    })
    assert wr_config.resolve_telegram_token(root) == "NEW-TOKEN"


def test_resolve_telegram_token_legacy_path(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {
        "telegram": {"bot_token": "LEGACY-TOKEN"},
    })
    assert wr_config.resolve_telegram_token(root) == "LEGACY-TOKEN"


def test_resolve_telegram_token_missing(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path, {"llm": {"model": "x"}})
    assert wr_config.resolve_telegram_token(root) is None


def test_malformed_yaml_is_tolerated(tmp_path: Path) -> None:
    ws = tmp_path / "default_workspace"
    ws.mkdir()
    (ws / "config.user.yaml").write_text("model: : : broken")
    # Should not raise — returns default
    assert wr_config.resolve_model(tmp_path) == wr_config.DEFAULT_MODEL
