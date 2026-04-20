from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.integrations.pi_company import bootstrap_repo, get_status


def _make_package(package_dir: Path) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": "@mariozechner/pi-company", "version": "0.67.68"})
    )


def test_bootstrap_repo_writes_settings_and_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = tmp_path / "repo"
    package_dir = tmp_path / "pi-mono" / "packages" / "pi-company"

    workspace.mkdir()
    target.mkdir()
    _make_package(package_dir)

    result = bootstrap_repo(
        target_path=target,
        workspace=workspace,
        package_path=str(package_dir),
        company_name="DansLab",
    )

    settings = json.loads(result.settings_path.read_text())
    assert str(package_dir.resolve()) in settings["packages"]
    context = result.context_path.read_text()
    assert "# DansLab Repo Context" in context
    assert "This file was bootstrapped by SemeClaw" in context


def test_status_detects_existing_bootstrap(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = tmp_path / "repo"
    package_dir = tmp_path / "pi-mono" / "packages" / "pi-company"

    workspace.mkdir()
    target.mkdir()
    _make_package(package_dir)

    bootstrap_repo(target_path=target, workspace=workspace, package_path=str(package_dir))
    status = get_status(target_path=target, workspace=workspace, package_path=str(package_dir))

    assert status.package_configured is True
    assert status.context_exists is True
    assert status.package_path == package_dir
