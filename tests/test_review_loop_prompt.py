from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.integrations.review_loop import install_review_loop


def test_review_loop_prompt_includes_skill_memory_and_notify(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    workspace.mkdir()
    repo.mkdir()

    result = install_review_loop(workspace=workspace, repo_path=repo, force=True)
    prompt = result.prompt

    assert "get_skill" in prompt
    assert "repo-review-loop" in prompt
    assert "memory_save" in prompt
    assert "telegram_notify" in prompt
    assert "Read the bundle file" in prompt
