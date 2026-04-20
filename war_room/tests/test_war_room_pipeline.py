"""Integration-ish tests for WarRoomPipeline with the LLM call stubbed out."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Import war_room/war_room.py as a plain module named `war_room_module` so it
# doesn't collide with the `war_room` package (which is this tests' parent).
_WAR_ROOM_PY = Path(__file__).resolve().parent.parent / "war_room.py"
_spec = importlib.util.spec_from_file_location("war_room_module", _WAR_ROOM_PY)
wr = importlib.util.module_from_spec(_spec)
sys.modules["war_room_module"] = wr
_spec.loader.exec_module(wr)  # type: ignore[union-attr]


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect STATE_FILE / LOGS_DIR / RESEARCH_DIR / TELEGRAM_CHAT_FILE to tmp."""
    state_file = tmp_path / "shared_state.json"
    logs_dir = tmp_path / "logs"
    research_dir = tmp_path / "research"
    logs_dir.mkdir()
    research_dir.mkdir()
    monkeypatch.setattr(wr, "STATE_FILE", state_file)
    monkeypatch.setattr(wr, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(wr, "RESEARCH_DIR", research_dir)
    monkeypatch.setattr(wr, "TELEGRAM_CHAT_FILE", tmp_path / ".telegram_chat_id")
    return state_file


def test_slugify_basics() -> None:
    assert wr._slugify("Hello World") == "hello-world"
    assert wr._slugify("Research: AI agents!") == "research-ai-agents"
    # All-punctuation falls back to "task"
    assert wr._slugify("!!!") == "task"
    # Honours length cap
    assert len(wr._slugify("x" * 200, max_len=40)) <= 40


def test_write_report_file_has_front_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wr, "RESEARCH_DIR", tmp_path)
    out = wr._write_report_file(
        task="Find gaps",
        agents=["research", "writer"],
        started_iso="2026-04-16T12:00:00",
        results={"research": "research body", "writer": "writer body"},
    )
    text = out.read_text()
    assert text.startswith("---")
    assert "task:" in text
    assert "agents: [research, writer]" in text
    assert "## Research Agent Output" in text
    assert "writer body" in text


def test_pipeline_run_records_state_and_writes_report(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub call_agent to avoid any LLM calls
    async def fake_call_agent(agent_id: str, task: str, context: str = "", model: str | None = None) -> str:
        return f"[{agent_id}] response for {task!r}"

    # Writer returns structured output so parsing can extract AC
    async def fake_call_agent_mixed(agent_id: str, task: str, context: str = "", model: str | None = None) -> str:
        if agent_id == "writer":
            return (
                "# Shipping Telemetry Spec\n\n"
                "Context: build a telemetry pipeline.\n\n"
                "## Acceptance Criteria\n"
                "- [ ] Emit metrics every 30s\n"
                "- [ ] Dashboard lives at /telemetry\n"
            )
        return f"[{agent_id}] response"

    monkeypatch.setattr(wr, "call_agent", fake_call_agent_mixed)

    pipeline = wr.WarRoomPipeline(model="anthropic/claude-sonnet-4-6")
    result = asyncio.run(pipeline.run(
        task="Build telemetry",
        agents=["research", "writer"],
        project="NERVIX",
        notify_telegram=False,
    ))

    assert result.run_id
    assert result.paperclip_issue is not None
    issue = result.paperclip_issue
    assert issue["title"] == "Shipping Telemetry Spec"
    assert issue["acceptance_criteria"] == [
        "Emit metrics every 30s",
        "Dashboard lives at /telemetry",
    ]
    # Report saved
    assert result.output_file.exists()
    # State updated
    from state import load_state
    state = load_state(isolated_state)
    assert state["metrics"]["tasks_run"] == 1
    assert state["metrics"]["tasks_succeeded"] == 1
    assert state["metrics"]["paperclip_issues_created"] == 1


def test_pipeline_skips_telegram_when_chat_file_missing(
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wr, "call_agent", AsyncMock(return_value="done"))
    # TELEGRAM_CHAT_FILE is set to a non-existing path by isolated_state
    pipeline = wr.WarRoomPipeline()
    with patch.object(wr, "PaperclipAdapter") as mock_pc_cls, \
         patch.object(wr, "MulticaAdapter") as mock_mc_cls:
        mock_pc = mock_pc_cls.return_value
        mock_pc.create_issue = AsyncMock(return_value={"id": "PC-999", "title": "X"})
        mock_pc.close = AsyncMock()
        mock_mc = mock_mc_cls.return_value
        mock_mc.create_issue = AsyncMock(return_value=None)
        mock_mc.close = AsyncMock()

        asyncio.run(pipeline.run(
            task="X",
            agents=["research", "writer"],
            project="NERVIX",
            notify_telegram=True,
        ))
        mock_pc.create_issue.assert_awaited_once()
        mock_pc.close.assert_awaited_once()
