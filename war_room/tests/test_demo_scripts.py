"""Tests for war_room/dashboard/demo_scripts.py"""

from __future__ import annotations

import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

import demo_scripts

# ---------------------------------------------------------------------------
# Scenario listing + basic shape
# ---------------------------------------------------------------------------


def test_list_scenarios_returns_known_ids():
    ids = {s["id"] for s in demo_scripts.list_scenarios()}
    assert ids == {
        "welcome",
        "nervix-marketplaces",
        "telemetry-stack",
        "crawdbot-pricing",
        "api-design-review",
        "launch-readiness",
    }


def test_every_scenario_has_intro_and_outro():
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        kinds = [t["kind"] for t in s["turns"]]
        assert kinds[0] == "intro", f"{sid} must start with an intro"
        assert kinds[-1] == "outro", f"{sid} must end with an outro"


def test_get_scenario_unknown_returns_none():
    assert demo_scripts.get_scenario("bogus") is None


# ---------------------------------------------------------------------------
# Narrator intro / outro
# ---------------------------------------------------------------------------


def test_intro_message_is_materialized_with_problem_and_roster():
    s = demo_scripts.get_scenario("nervix-marketplaces")
    intro = s["turns"][0]
    assert intro["agent"] == "narrator"
    assert intro["voice"] == demo_scripts.VOICES["narrator"]
    msg = intro["message"]
    assert "NERVIX" in msg
    for agent in s["agents"]:
        assert demo_scripts.DISPLAY_NAMES[agent] in msg


def test_outro_references_the_deliverable():
    s = demo_scripts.get_scenario("telemetry-stack")
    outro = s["turns"][-1]
    assert outro["agent"] == "narrator"
    assert "architecture" in outro["message"]


# ---------------------------------------------------------------------------
# Voice assignment
# ---------------------------------------------------------------------------


def test_every_speaking_turn_has_a_voice():
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        for t in s["turns"]:
            if t.get("kind") in ("speak", "handoff", "intro", "outro"):
                assert t.get("voice"), f"{sid} turn missing voice: {t}"


def test_each_agent_in_a_scenario_has_a_distinct_voice():
    """Voices should be distinct across agents so handoffs are audibly clear."""
    s = demo_scripts.get_scenario("nervix-marketplaces")
    voices_by_agent = {}
    for t in s["turns"]:
        if t["kind"] == "speak":
            voices_by_agent[t["agent"]] = t["voice"]
    distinct = set(voices_by_agent.values())
    assert len(distinct) == len(voices_by_agent), f"Expected one distinct voice per speaker, got {voices_by_agent}"


def test_voices_span_multiple_accents():
    """Distinctive accents (US, UK, AU) make the agents sound like different people."""
    locales = set()
    for voice in demo_scripts.VOICES.values():
        # e.g. en-US-SteffanNeural  →  en-US
        locale = "-".join(voice.split("-")[:2])
        locales.add(locale)
    assert len(locales) >= 3, f"Expected multiple accents, got {locales}"


def test_prosody_attached_to_speaking_turns():
    """Every speaker turn should carry rate + pitch for edge-tts prosody."""
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        for t in s["turns"]:
            if t["kind"] in ("speak", "intro", "outro", "handoff"):
                assert "rate" in t, f"{sid}: missing rate on turn"
                assert "pitch" in t, f"{sid}: missing pitch on turn"


# ---------------------------------------------------------------------------
# Dialog quality — turns must reference each other
# ---------------------------------------------------------------------------


def test_handoff_turn_names_the_next_speaker():
    """Explicit handoffs (e.g. 'Over to you, Hermes') make the workflow audible."""
    s = demo_scripts.get_scenario("nervix-marketplaces")
    turns = s["turns"]
    for i, t in enumerate(turns):
        if t["kind"] != "handoff":
            continue
        next_speaker = None
        for later in turns[i + 1 :]:
            if later["kind"] in ("speak", "outro"):
                next_speaker = later["agent"]
                break
        assert next_speaker, f"handoff at idx {i} has no follow-up speaker"
        if next_speaker != "narrator":
            assert demo_scripts.DISPLAY_NAMES[next_speaker] in t["message"], (
                f"handoff should name {demo_scripts.DISPLAY_NAMES[next_speaker]}"
            )


def test_every_scenario_has_multi_round_dialog():
    """Real dialog means at least one agent speaks more than once."""
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        counts = {}
        for t in s["turns"]:
            if t["kind"] == "speak":
                counts[t["agent"]] = counts.get(t["agent"], 0) + 1
        assert any(c > 1 for c in counts.values()), f"{sid}: no agent speaks twice — not a dialog, it's a relay"


def test_dialog_turns_reference_peer_agents():
    """At least one speak turn per scenario should name another attendee."""
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        peer_mentions = 0
        for t in s["turns"]:
            if t["kind"] != "speak":
                continue
            for other in s["agents"]:
                if other == t["agent"]:
                    continue
                if demo_scripts.DISPLAY_NAMES[other].lower() in (t["message"] or "").lower():
                    peer_mentions += 1
                    break
        assert peer_mentions >= 2, f"{sid}: expected at least 2 peer-mention turns (dialog), saw {peer_mentions}"


# ---------------------------------------------------------------------------
# Meeting card + why_assigned
# ---------------------------------------------------------------------------


def test_meeting_card_has_title_agenda_attendees():
    s = demo_scripts.get_scenario("nervix-marketplaces")
    mc = s["meeting_card"]
    assert mc["title"]
    assert mc["agenda"]
    assert len(mc["attendees"]) == len(s["agents"])
    for att in mc["attendees"]:
        assert att["name"]
        assert att["role"]
        assert att["voice"]
        assert att["emoji"]


def test_meeting_card_attendees_carry_why_assigned():
    s = demo_scripts.get_scenario("nervix-marketplaces")
    for att in s["meeting_card"]["attendees"]:
        assert att["why"], f"attendee {att['id']} missing 'why'"
        # why_assigned should introduce the agent by name
        assert att["name"].lower() in att["why"].lower()


def test_every_scenario_has_why_assigned_for_every_agent():
    for sid in demo_scripts.SCENARIOS:
        s = demo_scripts.get_scenario(sid)
        for agent in s["agents"]:
            bio = demo_scripts.get_why_assigned(sid, agent)
            assert bio is not None, f"{sid}/{agent} has no why_assigned"
            assert bio["text"]
            assert bio["voice"] == demo_scripts.VOICES[agent]


def test_get_why_assigned_for_unknown_scenario_returns_none():
    assert demo_scripts.get_why_assigned("bogus", "research") is None


def test_get_why_assigned_for_unknown_agent_returns_none():
    assert demo_scripts.get_why_assigned("nervix-marketplaces", "ghost") is None
