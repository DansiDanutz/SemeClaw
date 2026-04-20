from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.integrations.github_pr import get_pr_status, write_review_bundle, write_review_bundle_or_local


class _Completed:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_get_pr_status_uses_gh_json_payloads(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    pr_payload = {
        "number": 42,
        "title": "Tighten auth flow",
        "url": "https://github.com/acme/repo/pull/42",
        "headRefName": "feature/auth",
        "baseRefName": "main",
        "state": "OPEN",
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "BLOCKED",
        "changedFiles": 3,
        "additions": 120,
        "deletions": 14,
        "author": {"login": "davidai"},
        "files": [{"path": "src/auth.py"}, {"path": "tests/test_auth.py"}],
    }
    checks_payload = [{"name": "ci", "bucket": "pass", "state": "SUCCESS"}]

    with patch("semeclaw.integrations.github_pr.subprocess.run") as run:
        run.side_effect = [
            _Completed(json.dumps(pr_payload)),
            _Completed(json.dumps(checks_payload)),
        ]
        status = get_pr_status(repo)

    assert status.number == 42
    assert status.review_decision == "REVIEW_REQUIRED"
    assert status.files[0]["path"] == "src/auth.py"
    assert status.checks[0]["bucket"] == "pass"


def test_write_review_bundle_creates_markdown_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    pr_payload = {
        "number": 7,
        "title": "Improve release verification",
        "url": "https://github.com/acme/repo/pull/7",
        "headRefName": "feature/release",
        "baseRefName": "main",
        "state": "OPEN",
        "reviewDecision": None,
        "mergeStateStatus": "CLEAN",
        "changedFiles": 1,
        "additions": 20,
        "deletions": 5,
        "author": {"login": "dan"},
        "files": [{"path": "release.py"}],
    }
    checks_payload = [{"name": "test", "bucket": "pending", "state": "IN_PROGRESS"}]
    diff_payload = "diff --git a/release.py b/release.py\n+print('ok')\n"

    with patch("semeclaw.integrations.github_pr.subprocess.run") as run:
        run.side_effect = [
            _Completed(json.dumps(pr_payload)),
            _Completed(json.dumps(checks_payload)),
            _Completed(diff_payload),
        ]
        bundle = write_review_bundle(repo)

    content = bundle.output_path.read_text()
    assert bundle.output_path.exists()
    assert "PR Review Bundle: #7 Improve release verification" in content
    assert "Use this bundle with `pi-company` and the `pr-review` skill." in content
    assert "release.py" in content


def test_write_review_bundle_falls_back_to_local_diff_when_no_pr_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("semeclaw.integrations.github_pr.subprocess.run") as run:
        run.side_effect = [
            _Completed("", returncode=1, stderr='no pull requests found for branch "main"'),
            _Completed("main\n"),
            _Completed(" M src/semeclaw/core/agent.py\n"),
            _Completed(" src/semeclaw/core/agent.py | 2 ++\n 1 file changed, 2 insertions(+)\n"),
            _Completed("diff --git a/src/semeclaw/core/agent.py b/src/semeclaw/core/agent.py\n+test\n"),
        ]
        bundle = write_review_bundle_or_local(repo)

    content = bundle.output_path.read_text()
    assert "Local Review Bundle: main" in content
    assert "No open PR was found for this branch." in content
    assert "src/semeclaw/core/agent.py" in content
