"""Repository-level regression guard for provider credentials."""

from __future__ import annotations

import re
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
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix not in SCANNED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            findings.append(str(path.relative_to(ROOT)))
    assert not findings, f"provider credential patterns found in: {findings}"
