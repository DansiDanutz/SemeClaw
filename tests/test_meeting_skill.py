"""
Unit tests for war_room/dashboard/meeting_skill.py — the pure module that
converts markdown reports into scripted meeting segments.

Run: uv run pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make war_room importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "war_room" / "dashboard"))

import pytest  # noqa: E402
from meeting_skill import (  # noqa: E402
    DAN_SPEAKER,
    HOST_SPEAKER,
    ORCHESTRATOR_SPEAKER,
    MeetingScript,
    Segment,
    build_script,
    parse_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_report() -> str:
    return """# Test Report

**Task:** What is the GTM strategy for NERVIX?
**Date:** 2026-04-18

---

## Research Agent Output

NERVIX is a new AI agent marketplace. The target market is Paperclip companies
and individual creators. Competitors include OpenAI GPTs and Hugging Face Spaces.

## Strategist Agent Output

Lead with 3 distinct value props: one-click embed, multi-tenant isolation,
per-meeting pricing transparency. Go-to-market via Paperclip network first.

## Writer Agent Output

Draft a 2-week launch plan: landing page, Product Hunt post, 5 pilot customers
from Paperclip ecosystem, webinar with Dan speaking.
"""


@pytest.fixture
def minimal_report() -> str:
    return """# Report
**Task:** minimal
## Research
Just one section
"""


# ---------------------------------------------------------------------------
# parse_report
# ---------------------------------------------------------------------------

class TestParseReport:
    def test_extracts_sections(self, sample_report):
        sections = parse_report(sample_report)
        # 3 agent sections: Research → Autoresearch, Strategist → GSD, Writer → Hermes
        assert len(sections) == 3
        speakers = [s[0] for s in sections]
        assert "Autoresearch" in speakers
        assert "GSD" in speakers
        assert "Hermes" in speakers

    def test_skips_metadata_lines(self, sample_report):
        sections = parse_report(sample_report)
        all_text = " ".join(text for _, text in sections)
        assert "**Task:**" not in all_text
        assert "**Date:**" not in all_text
        assert "2026-04-18" not in all_text

    def test_caps_section_length(self):
        long_content = "# Report\n\n## Research\n\n" + ("x" * 2000)
        sections = parse_report(long_content, max_chars_per_section=450)
        assert len(sections) == 1
        _, text = sections[0]
        assert len(text) <= 450

    def test_routes_unknown_section_to_narrator(self):
        content = "# Report\n\n## Gibberish Section\n\nContent with enough length to pass filter."
        sections = parse_report(content)
        assert len(sections) == 1
        assert sections[0][0] == "Narrator"

    def test_drops_too_short_sections(self):
        content = "# Report\n\n## Research\n\nhi"  # <20 chars after cleaning
        sections = parse_report(content)
        assert sections == []

    def test_handles_empty_content(self):
        assert parse_report("") == []
        assert parse_report("# Just a title") == []


# ---------------------------------------------------------------------------
# build_script
# ---------------------------------------------------------------------------

class TestBuildScript:
    def test_returns_meeting_script(self, sample_report):
        s = build_script(
            report_content=sample_report,
            task="What is NERVIX's GTM?",
            meeting_id="abc12345",
        )
        assert isinstance(s, MeetingScript)
        assert s.meeting_id == "abc12345"
        assert s.subject == "What is NERVIX's GTM?"

    def test_attendees_always_include_dan_and_orchestrator(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        assert DAN_SPEAKER in s.attendees
        assert ORCHESTRATOR_SPEAKER in s.attendees

    def test_attendees_include_section_speakers(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        assert "Autoresearch" in s.attendees
        assert "GSD" in s.attendees
        assert "Hermes" in s.attendees

    def test_first_segment_is_host_announcement(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        assert s.segments[0].speaker == HOST_SPEAKER
        assert s.segments[0].role == "host"
        # Host mentions meeting number, subject, attendees
        assert "meeting" in s.segments[0].text.lower()
        assert "test" in s.segments[0].text.lower()

    def test_last_segment_is_dan_adjourning(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        last = s.segments[-1]
        assert last.speaker == DAN_SPEAKER
        assert last.role == "dan"

    def test_orchestrator_handoff_before_each_agent(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        # Iterate pairs — every agent should be preceded by an orchestrator handoff
        for i, seg in enumerate(s.segments):
            if seg.role == "agent":
                prev = s.segments[i - 1]
                assert prev.role == "orchestrator", (
                    f"agent {seg.speaker} not preceded by orchestrator handoff "
                    f"(got {prev.role}:{prev.speaker})"
                )

    def test_total_segment_count_matches_formula(self, sample_report):
        """For N agent sections → 1 host + 1 orch_open + (N × (handoff+agent)) + 1 orch_close + 1 dan"""
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        n_agents = 3  # Research, Strategist, Writer
        expected = 1 + 1 + (2 * n_agents) + 1 + 1
        assert len(s.segments) == expected, f"got {len(s.segments)} segments, expected {expected}"

    def test_seed_produces_deterministic_phrases(self, sample_report):
        s1 = build_script(report_content=sample_report, task="t", meeting_id="x", seed=42)
        s2 = build_script(report_content=sample_report, task="t", meeting_id="x", seed=42)
        texts1 = [seg.text for seg in s1.segments]
        texts2 = [seg.text for seg in s2.segments]
        assert texts1 == texts2

    def test_different_seeds_produce_different_phrases(self, sample_report):
        s1 = build_script(report_content=sample_report, task="t", meeting_id="x", seed=1)
        s2 = build_script(report_content=sample_report, task="t", meeting_id="x", seed=999)
        # Orchestrator opener is randomized — should differ most of the time
        orch_segs_1 = [s.text for s in s1.segments if s.role == "orchestrator"]
        orch_segs_2 = [s.text for s in s2.segments if s.role == "orchestrator"]
        assert orch_segs_1 != orch_segs_2

    def test_handles_single_agent_section(self, minimal_report):
        s = build_script(report_content=minimal_report, task="minimal", meeting_id="x")
        assert len(s.segments) >= 4  # host + opener + handoff + agent + closer + dan minimum
        assert s.segments[0].role == "host"
        assert s.segments[-1].role == "dan"

    def test_handles_zero_agent_sections(self):
        empty_report = "# Report\n**Task:** nothing\n"
        s = build_script(report_content=empty_report, task="nothing", meeting_id="x")
        # Still gets host + orchestrator opener + orch closer + dan
        assert len(s.segments) >= 4
        assert s.segments[0].role == "host"
        assert s.segments[-1].role == "dan"

    def test_meeting_id_is_cleaned(self):
        s = build_script(
            report_content="# Report\n## Research\n\nsomething with enough content to pass",
            task="t",
            meeting_id="abc-123!@#$%",
        )
        # Only alphanumeric kept, capped at 8
        assert s.meeting_id == "abc123"

    def test_extra_attendees_merged(self, sample_report):
        s = build_script(
            report_content=sample_report, task="t", meeting_id="x",
            extra_attendees=["Dexter", "Memo"],
        )
        assert "Dexter" in s.attendees
        assert "Memo" in s.attendees

    def test_all_segments_have_non_empty_text(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        for seg in s.segments:
            assert isinstance(seg.text, str)
            assert len(seg.text.strip()) > 0

    def test_segments_serialize_to_json_safe_dict(self, sample_report):
        s = build_script(report_content=sample_report, task="test", meeting_id="x")
        d = s.to_dict()
        assert isinstance(d, dict)
        assert "segments" in d
        assert all(isinstance(s, dict) for s in d["segments"])
        # No non-primitive values
        import json as _json
        _json.dumps(d)  # must not raise


class TestSegment:
    def test_default_pause(self):
        seg = Segment(speaker="Dan", text="hi")
        assert seg.pause_ms_after == 200

    def test_to_dict_roundtrip(self):
        seg = Segment(speaker="Dan", text="hi", pause_ms_after=400, role="dan")
        d = seg.to_dict()
        assert d == {"speaker": "Dan", "text": "hi", "pause_ms_after": 400, "role": "dan"}
