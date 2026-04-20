"""Tests for war_room.parsing — writer-output → issue extraction."""

from __future__ import annotations

from parsing import parse_writer_output


def test_empty_output_uses_fallback_task() -> None:
    result = parse_writer_output("", "Audit telemetry")
    assert "Audit telemetry" in result.title
    assert result.description == "Audit telemetry"
    assert result.acceptance_criteria == ()


def test_title_from_explicit_title_line() -> None:
    text = """Some intro.
Title:  Build Nervix billing dashboard
Some more prose.
"""
    result = parse_writer_output(text, "fallback")
    assert result.title == "Build Nervix billing dashboard"


def test_title_from_first_h1() -> None:
    text = "# Agent Marketplace Research\n\nBody content here."
    result = parse_writer_output(text, "fallback")
    assert result.title == "Agent Marketplace Research"
    assert "Body content here" in result.description
    # H1 should be stripped from description
    assert not result.description.startswith("# ")


def test_acceptance_criteria_extracted() -> None:
    text = """# Issue

Description paragraph.

## Acceptance Criteria

- [ ] Metric endpoint is exposed
- [ ] Dashboard shows daily active users
- [ ] Alert fires when errors >1%
"""
    result = parse_writer_output(text, "fallback")
    assert len(result.acceptance_criteria) == 3
    assert result.acceptance_criteria[0].startswith("Metric endpoint")
    # Description should stop before AC section
    assert "Acceptance Criteria" not in result.description
    assert "Metric endpoint" not in result.description


def test_acceptance_criteria_deduped() -> None:
    text = """# X

- [ ] Same task
- [ ] Same task
- [ ] Different task
"""
    result = parse_writer_output(text, "fallback")
    assert list(result.acceptance_criteria) == ["Same task", "Different task"]


def test_title_truncated_with_ellipsis() -> None:
    long = "A" * 500
    result = parse_writer_output(long, "fallback")
    assert len(result.title) <= 141  # max + ellipsis
    assert result.title.endswith("…")


def test_description_limited() -> None:
    body = "Intro line.\n" + ("prose line\n" * 5000)
    result = parse_writer_output(body, "fallback")
    assert len(result.description) <= 2000


def test_as_tuple_compat_shape() -> None:
    result = parse_writer_output("# T\n\n- [ ] one", "fallback")
    title, desc, ac = result.as_tuple()
    assert title == "T"
    assert isinstance(ac, list)
    assert ac == ["one"]
