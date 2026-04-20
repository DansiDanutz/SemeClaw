from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.utils.config import Config


def test_config_load_falls_back_to_config_yaml(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "\n".join(
            [
                "llm:",
                "  provider: dashscope",
                "  model: qwen-plus",
                "  api_key: test-key",
                "default_agent: seme",
            ]
        )
    )

    config = Config.load(workspace)

    assert config.default_agent == "seme"
    assert config.llm.model == "qwen-plus"


def test_config_user_yaml_still_takes_precedence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "\n".join(
            [
                "llm:",
                "  provider: dashscope",
                "  model: qwen-plus",
                "  api_key: base-key",
                "default_agent: seme",
            ]
        )
    )
    (workspace / "config.user.yaml").write_text(
        "\n".join(
            [
                "llm:",
                "  provider: openrouter",
                "  model: openrouter/qwen/qwen3.6-plus:free",
                "  api_key: user-key",
                "default_agent: researcher",
            ]
        )
    )

    config = Config.load(workspace)

    assert config.default_agent == "researcher"
    assert config.llm.provider == "openrouter"
