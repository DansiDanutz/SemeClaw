"""Tests for CrewAI pipeline integration."""

from __future__ import annotations


def test_load_crewai_runner():
    """Adapter module imports cleanly."""
    from semeclaw.integrations.crewai_runner import (
        _parse_agent_md,
        build_crewai_agent,
        build_crewai_task,
        run_crewai_pipeline,
    )
    assert callable(_parse_agent_md)
    assert callable(build_crewai_agent)
    assert callable(build_crewai_task)
    assert callable(run_crewai_pipeline)


def test_parse_agent_md_extracts_frontmatter():
    import tempfile
    from pathlib import Path

    from semeclaw.integrations.crewai_runner import _parse_agent_md

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "agents"
        path.mkdir()
        (path / "test_agent.md").write_text(
            "---\nrole: Test Role\nname: Test Agent\n---\n\n# Body\nThis is the body.",
            encoding="utf-8",
        )
        fm, body = _parse_agent_md("test_agent", agents_dir=path)
        assert fm.get("role") == "Test Role"
        assert "This is the body" in body


def test_extract_goal_and_backstory():
    from semeclaw.integrations.crewai_runner import _extract_goal_and_backstory
    body = "## Role & Expertise\nYou are a test agent.\n\n## System Prompt\nDo things."
    goal, backstory = _extract_goal_and_backstory(body, "test")
    assert "test agent" in goal or "Do things" in goal
    assert "System Prompt" in backstory


def test_crewai_pipeline_class_imports():
    from war_room.pipelines.crewai_pipeline import CrewAIPipeline
    assert hasattr(CrewAIPipeline, "run")


def test_get_pipeline_factory():
    from war_room.war_room import WarRoomPipeline, get_pipeline
    native = get_pipeline("native")
    assert isinstance(native, WarRoomPipeline)
    # CrewAI pipeline requires imports; just verify factory dispatches
    crewai = get_pipeline("crewai")
    assert crewai.__class__.__name__ == "CrewAIPipeline"
