"""Repository-level regression guard for provider credentials."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED_SUFFIXES = {".py", ".toml", ".yaml", ".yml", ".json", ".md", ".env"}
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-kimi-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(?:zhipu|bigmodel).{0,40}[=:]\s*[\"'][A-Za-z0-9]{24,}[\"']"),
)


def test_repository_has_no_provider_credentials() -> None:
    findings: list[str] = []
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for relative_path in tracked:
        if not relative_path:
            continue
        path = ROOT / relative_path.decode("utf-8", errors="surrogateescape")
        if path.suffix not in SCANNED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            findings.append(str(path.relative_to(ROOT)))
    assert not findings, f"provider credential patterns found in: {findings}"


def test_supported_install_paths_do_not_enable_quarantined_extras() -> None:
    result = subprocess.run(
        ["git", "grep", "-l", "--", "--all-extras"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    this_test = str(Path(__file__).resolve().relative_to(ROOT))
    offenders = [path for path in result.stdout.splitlines() if path != this_test]
    assert not offenders, f"quarantined extras enabled by default in: {offenders}"
