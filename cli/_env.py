"""Read/write the user-local SemeClaw env file (~/.semeclaw/env).

Format: simple KEY=VALUE per line. Values are unquoted unless they contain
spaces. Comments start with #.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_DIR = Path.home() / ".semeclaw"
ENV_FILE = ENV_DIR / "env"


def load() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save(updates: dict[str, str]) -> Path:
    """Merge updates into the env file. Creates the dir + file if missing."""
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    current = load()
    current.update({k: v for k, v in updates.items() if v})
    lines = ["# SemeClaw user env — managed by `semeclaw setup`"]
    for k in sorted(current.keys()):
        v = current[k]
        if " " in v or "#" in v:
            v = f'"{v}"'
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)  # secrets — owner-only
    except Exception:
        pass
    return ENV_FILE


def export_into_process() -> None:
    """Load env file into os.environ (only keys not already set)."""
    for k, v in load().items():
        os.environ.setdefault(k, v)
