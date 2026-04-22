"""Regression guard for APP_VERSION resolution (no tomllib dependency)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from war_room.dashboard.routes.deps import APP_VERSION, _read_version


def test_app_version_is_not_fallback_sentinel():
    """0.0.0 means resolution failed — should never ship."""
    assert APP_VERSION != "0.0.0"


def test_app_version_matches_pyproject():
    """Version string must match pyproject.toml."""
    pyproject = ROOT / "pyproject.toml"
    txt = pyproject.read_text(encoding="utf-8")
    import re

    m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.MULTILINE)
    assert m is not None
    expected = m.group(1)
    assert APP_VERSION == expected


def test_read_version_returns_non_empty_string():
    """_read_version must return a non-empty string."""
    v = _read_version()
    assert isinstance(v, str)
    assert v
    assert v != "0.0.0"
