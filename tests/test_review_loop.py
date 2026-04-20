from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.integrations.review_loop import install_review_loop


def test_install_review_loop_writes_cron_yaml(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    workspace.mkdir()
    repo.mkdir()

    result = install_review_loop(
        workspace=workspace,
        repo_path=repo,
        schedule="15 10 * * 1-5",
    )

    data = yaml.safe_load(result.cron_path.read_text())
    assert data["id"] == "review-loop-repo"
    assert data["schedule"] == "15 10 * * 1-5"
    assert str(repo) in data["prompt"]
    assert "github_pr_review_bundle" in data["prompt"]
    assert "memory_save" in data["prompt"]
