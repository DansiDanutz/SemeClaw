"""Smoke tests for the versioning policy and the CI check script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_version_bumped.sh"
BUMP = ROOT / "scripts" / "bump_version.py"


def test_check_script_is_executable() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "check_version_bumped.sh should be chmod +x"


def test_bump_version_help() -> None:
    r = subprocess.run(
        ["python", str(BUMP), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Either parser prints help on --help or errors usefully — both acceptable.
    out = (r.stdout + r.stderr).lower()
    assert "patch" in out or "major" in out or "version" in out


def test_versioning_md_exists() -> None:
    md = ROOT / "VERSIONING.md"
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "semver" in text.lower() or "semantic versioning" in text.lower()
    assert "PATCH" in text and "MINOR" in text and "MAJOR" in text


def test_changelog_has_recent_entry() -> None:
    cl = ROOT / "CHANGELOG.md"
    text = cl.read_text(encoding="utf-8")
    # At least one versioned entry
    import re

    assert re.search(r"## \[\d+\.\d+\.\d+\]", text), "CHANGELOG has no versioned entries"
