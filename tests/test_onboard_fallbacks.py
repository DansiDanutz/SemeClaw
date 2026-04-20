from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.cli.onboard import PROVIDER_BY_ID, write_config


def test_write_config_seeds_fallback_chain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    env = {
        "OPENROUTER_API_KEY": "or-key",
        "OLLAMA_BASE_URL": "http://localhost:11434",
    }

    with patch.dict(os.environ, env, clear=False), patch(
        "semeclaw.cli.onboard._probe_http", return_value=True
    ):
        config_path = write_config(
            workspace=workspace,
            provider=PROVIDER_BY_ID["dashscope"],
            model="qwen-plus",
            api_key="dash-key",
            default_agent="seme",
        )

    config = yaml.safe_load(config_path.read_text())
    fallbacks = config["llm"]["fallbacks"]

    assert fallbacks[0]["provider"] == "openrouter"
    assert fallbacks[1]["provider"] == "ollama"
