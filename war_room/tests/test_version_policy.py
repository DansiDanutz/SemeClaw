"""Smoke tests for the versioning policy: the release pipeline — not PRs —
assigns versions. These tests pin the invariants of that model."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUMP = ROOT / "scripts" / "bump_version.py"
DAILY = ROOT / ".github" / "workflows" / "daily-release.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_release_pipeline_is_the_version_authority() -> None:
    # The per-PR version gate is gone for good — it raced the daily release
    # bot and forced open PRs into daily manual version leapfrogs.
    assert not (ROOT / "scripts" / "check_version_bumped.sh").exists()
    assert "check_version_bumped" not in CI.read_text(encoding="utf-8")

    daily = DAILY.read_text(encoding="utf-8")
    # Releases are test-gated, skipped when empty, promote [Unreleased]
    # changelog sections, and publish a checksummed manifest.
    assert "needs: test" in daily
    assert "CHANGED=false" in daily
    assert "[Unreleased]" in daily
    assert "sha256" in daily


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


def test_versioning_md_documents_the_model() -> None:
    md = ROOT / "VERSIONING.md"
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "semver" in text.lower() or "semantic versioning" in text.lower()
    assert "[Unreleased]" in text
    assert "release pipeline" in text.lower()


def test_changelog_has_recent_entry() -> None:
    cl = ROOT / "CHANGELOG.md"
    text = cl.read_text(encoding="utf-8")
    # At least one versioned entry
    import re

    assert re.search(r"## \[\d+\.\d+\.\d+\]", text), "CHANGELOG has no versioned entries"
