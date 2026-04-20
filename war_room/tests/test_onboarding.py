"""Tests for SemeClaw onboarding discovery and sync."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from semeclaw.onboarding.discovery import (
    DiscoveredAgent,
    discover_all,
    discover_generic_agents,
    discover_hermes,
    discover_multica,
    discover_openclaw,
    discover_paperclip,
    to_json,
)
from semeclaw.onboarding.sync import clear_discovered, sync_agents


class TestDiscovery:
    def test_discovered_agent_dataclass(self):
        a = DiscoveredAgent(id="x", name="X", kind="agent", version="1.0")
        assert a.id == "x"
        assert a.status == "unknown"

    def test_to_json_serializes(self):
        agents = [DiscoveredAgent(id="a", name="A", kind="agent", version="1.0")]
        data = to_json(agents)
        assert data[0]["id"] == "a"
        assert data[0]["version"] == "1.0"

    def test_discover_openclaw_dot_dir(self, tmp_path):
        with patch("semeclaw.onboarding.discovery.HOME", tmp_path):
            (tmp_path / ".openclaw").mkdir()
            found = discover_openclaw()
        assert any(a.id == "openclaw" for a in found)

    def test_discover_openclaw_logger(self, tmp_path):
        with patch("semeclaw.onboarding.discovery.HOME", tmp_path):
            logger_dir = tmp_path / "openclaw-logger"
            logger_dir.mkdir()
            (logger_dir / "package.json").write_text('{"version": "2.1.0"}')
            found = discover_openclaw()
        assert any(a.id == "openclaw-logger" and a.version == "2.1.0" for a in found)

    def test_discover_paperclip_running(self, tmp_path):
        with patch("semeclaw.onboarding.discovery._check_tcp_port", return_value=True):
            found = discover_paperclip()
        assert any(a.id == "paperclip" and a.status == "running" for a in found)

    def test_discover_hermes_directory(self, tmp_path):
        with patch("semeclaw.onboarding.discovery.HOME", tmp_path):
            (tmp_path / ".hermes").mkdir()
            found = discover_hermes()
        assert any(a.id == "hermes" for a in found)

    def test_discover_multica_directory(self, tmp_path):
        with patch("semeclaw.onboarding.discovery.HOME", tmp_path):
            (tmp_path / "multica").mkdir()
            (tmp_path / "multica" / "pyproject.toml").write_text('version = "0.5.0"\n')
            found = discover_multica()
        assert any(a.id == "multica" and a.version == "0.5.0" for a in found)

    def test_discover_generic_agents_manifest(self, tmp_path):
        scan_root = tmp_path / "dev"
        scan_root.mkdir()
        agent_dir = scan_root / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.manifest.json").write_text(
            '{"name": "My Agent", "id": "my-agent", "version": "1.0"}'
        )
        with patch("semeclaw.onboarding.discovery._SCAN_ROOTS", [scan_root]):
            found = discover_generic_agents()
        assert any(a.id == "my-agent" and a.name == "My Agent" for a in found)

    def test_discover_all_runs_without_crash(self):
        found = discover_all()
        assert isinstance(found, list)


class TestSync:
    def test_sync_creates_markdown_and_state(self, tmp_path):
        with patch("semeclaw.onboarding.sync.STATE_FILE", tmp_path / "state.json"):
            with patch("semeclaw.onboarding.sync.AGENTS_DIR", tmp_path / "agents"):
                agents = [
                    DiscoveredAgent(id="alpha", name="Alpha", kind="agent", status="installed"),
                ]
                summary = sync_agents(agents)
                assert summary["created"] == ["alpha"]
                assert summary["total"] == 1

                md = (tmp_path / "agents" / "alpha.md").read_text()
                assert "Alpha" in md
                assert "Discovered via onboarding" in md

                state = json.loads((tmp_path / "state.json").read_text())
                assert state["discovered_agents"][0]["id"] == "alpha"
                assert "onboarding_last_run" in state

    def test_sync_dry_run_does_not_write(self, tmp_path):
        with patch("semeclaw.onboarding.sync.STATE_FILE", tmp_path / "state.json"):
            with patch("semeclaw.onboarding.sync.AGENTS_DIR", tmp_path / "agents"):
                agents = [DiscoveredAgent(id="beta", name="Beta", kind="agent")]
                sync_agents(agents, dry_run=True)
                assert not (tmp_path / "agents" / "beta.md").exists()
                assert not (tmp_path / "state.json").exists()

    def test_clear_discovered_removes_state_and_files(self, tmp_path):
        with patch("semeclaw.onboarding.sync.STATE_FILE", tmp_path / "state.json"):
            with patch("semeclaw.onboarding.sync.AGENTS_DIR", tmp_path / "agents"):
                # Setup
                agents = [DiscoveredAgent(id="gamma", name="Gamma", kind="agent")]
                sync_agents(agents)
                assert (tmp_path / "agents" / "gamma.md").exists()

                clear_discovered()
                state = json.loads((tmp_path / "state.json").read_text())
                assert "discovered_agents" not in state
                assert not (tmp_path / "agents" / "gamma.md").exists()
