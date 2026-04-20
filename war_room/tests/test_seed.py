"""Tests for built-in agent seeding."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.onboarding.seed import (
    BUILTIN_AGENTS,
    has_builtin_agents,
    seed_builtin_agents,
)


class TestSeed:
    def test_seed_creates_files(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            created = seed_builtin_agents()
        assert len(created) == 3
        for name in BUILTIN_AGENTS:
            assert (tmp_path / name).exists()

    def test_seed_skips_existing_by_default(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            seed_builtin_agents()
            created = seed_builtin_agents()
        assert created == []

    def test_seed_force_overwrites(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            seed_builtin_agents()
            (tmp_path / "research.md").write_text("old")
            created = seed_builtin_agents(force=True)
        assert "research" in created
        assert "Dexter" in (tmp_path / "research.md").read_text()

    def test_has_builtin_agents_true_when_all_present(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            seed_builtin_agents()
            assert has_builtin_agents() is True

    def test_has_builtin_agents_false_when_empty(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            assert has_builtin_agents() is False

    def test_has_builtin_agents_false_when_only_one(self, tmp_path):
        with patch("semeclaw.onboarding.seed.BUILTIN_AGENTS_DIR", tmp_path):
            (tmp_path / "research.md").write_text("x")
            assert has_builtin_agents() is False
