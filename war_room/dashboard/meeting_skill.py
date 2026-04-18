"""
Meeting Skill — turn a raw agent report into a natural conversational meeting.

Standardizes every War Room meeting with the same flow:
  1. Host announces the meeting (number, subject, attendees)
  2. Orchestrator opens and welcomes everyone
  3. Each agent section is introduced by the Orchestrator
  4. Orchestrator closes and hands back to Dan
  5. Dan adjourns

Input:  report markdown (## sections) + run_id + task
Output: list of {speaker, text, pause_ms_after} segments ready for TTS playback

Pure logic — no FastAPI / httpx deps. Tested via unit-style calls.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, asdict, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Speaker routing
# ---------------------------------------------------------------------------

HOST_SPEAKER = "Narrator"          # meeting announcer — neutral formal voice
ORCHESTRATOR_SPEAKER = "David"     # runs the meeting
DAN_SPEAKER = "Dan"                # the boss

# Section-heading keyword → speaker (must match _ELEVEN_VOICES in server.py)
_SECTION_ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"research", re.I),       "Autoresearch"),
    (re.compile(r"discovery", re.I),      "Discovery"),
    (re.compile(r"strategist|strategy", re.I), "GSD"),
    (re.compile(r"writer", re.I),         "Hermes"),
    (re.compile(r"architect", re.I),      "David"),
    (re.compile(r"coder|engineer", re.I), "Dexter"),
    (re.compile(r"doctor", re.I),         "Doctor"),
    (re.compile(r"monitor", re.I),        "Monitor"),
    (re.compile(r"finance", re.I),        "Finance"),
    (re.compile(r"growth", re.I),         "Growth"),
    (re.compile(r"learning|teacher", re.I), "Learning"),
    (re.compile(r"n8n|automation", re.I), "N8N"),
    (re.compile(r"obsidian|knowledge", re.I), "Obsidian"),
    (re.compile(r"codex", re.I),          "Codex"),
    (re.compile(r"claude", re.I),         "Claude Code"),
]


def _route_section(heading: str) -> str:
    for pat, speaker in _SECTION_ROUTES:
        if pat.search(heading):
            return speaker
    return "Narrator"


# ---------------------------------------------------------------------------
# Auxiliary phrase pools — keep each short enough for ~2s of speech
# ---------------------------------------------------------------------------

_ORCHESTRATOR_OPENERS = [
    "Thank you. Welcome everyone. {subject_line}",
    "Thanks for the intro. Alright team, let's get into it. {subject_line}",
    "Appreciated. Quick meeting today. {subject_line}",
    "Thanks. Welcome to the table. {subject_line}",
]

_SUBJECT_LINES = [
    "The topic on the table is: {subject}.",
    "Today's question: {subject}.",
    "Subject is: {subject}. Let's dig in.",
    "We're here to talk through: {subject}.",
]

_FIRST_HANDOFFS = [
    "{agent}, kick us off.",
    "{agent}, you're up first.",
    "{agent}, take it away.",
    "Over to you, {agent}.",
]

_MID_HANDOFFS = [
    "Thanks {prev}. {next}, your take?",
    "Good. {next}, where do you land on this?",
    "Noted. {next}, build on that.",
    "Understood. {next}, what is your angle?",
    "Solid. Over to you, {next}.",
    "Thanks {prev}. {next}?",
]

_LAST_TRANSITIONS = [
    "Last word goes to {next}.",
    "{next}, bring us home.",
    "Final take — {next}.",
]

_ORCHESTRATOR_CLOSERS = [
    "Alright. That is a clear picture. Dan, back to you.",
    "Good work, team. Dan, closing remarks?",
    "Solid lap. Dan, over to you.",
    "That gives us what we need. Dan?",
]

_DAN_CLOSERS = [
    "Perfect. Ship fast, stay sharp. Meeting adjourned.",
    "Love it. Let's execute. Meeting done.",
    "Clear. Let's get to work. Adjourned.",
    "Appreciated. Now let's build it. Meeting closed.",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    speaker: str
    text: str
    pause_ms_after: int = 200     # silence between speakers
    role: str = ""                # "host" | "orchestrator" | "agent" | "dan"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MeetingScript:
    meeting_id: str
    subject: str
    attendees: list[str]
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meeting_id": self.meeting_id,
            "subject":    self.subject,
            "attendees":  self.attendees,
            "segments":   [s.to_dict() for s in self.segments],
        }


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------


_META_PREFIXES = ("**Task:", "**Date:", "**Agents:", "---")


def _clean(text: str) -> str:
    """Strip markdown noise and collapse whitespace."""
    cleaned = re.sub(r"[`>]|#{1,3}\s*|\*\*|__|[\[\]]", "", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_report(content: str, max_chars_per_section: int = 450) -> list[tuple[str, str]]:
    """Split markdown into (speaker, text) tuples by ## headings. Skips metadata."""
    sections: list[tuple[str, list[str]]] = []
    current_speaker = "Narrator"
    current_buf: list[str] = []

    for line in content.splitlines():
        heading_match = re.match(r"^\s*#{1,3}\s+(.+)$", line)
        if heading_match:
            if current_buf:
                sections.append((current_speaker, current_buf))
            current_speaker = _route_section(heading_match.group(1))
            current_buf = []
            continue
        if any(line.lstrip().startswith(p) for p in _META_PREFIXES):
            continue
        if line.strip():
            current_buf.append(line)
    if current_buf:
        sections.append((current_speaker, current_buf))

    out: list[tuple[str, str]] = []
    for speaker, lines in sections:
        text = _clean(" ".join(lines))
        if len(text) < 20:
            continue
        if len(text) > max_chars_per_section:
            text = text[: max_chars_per_section - 1].rstrip() + "."
        out.append((speaker, text))
    return out


# ---------------------------------------------------------------------------
# Meeting script builder
# ---------------------------------------------------------------------------


def _attendee_list(attendees: Iterable[str]) -> str:
    names = list(dict.fromkeys(attendees))  # preserve order, unique
    if not names:
        return "the fleet"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + ", and " + names[-1]


def _short_meeting_id(meeting_id: str) -> str:
    """Return the short form: strip leading zeros, cap at 8 chars, ensure readability."""
    clean = re.sub(r"[^a-zA-Z0-9]", "", meeting_id)[:8] or meeting_id or "000"
    return clean


def build_script(
    *,
    report_content: str,
    task: str,
    meeting_id: str = "",
    extra_attendees: Iterable[str] = (),
    seed: int | None = None,
) -> MeetingScript:
    """Turn a raw agent report + metadata into a playable meeting script."""
    rng = random.Random(seed)

    agent_sections = parse_report(report_content)
    agent_speakers = [spk for spk, _ in agent_sections if spk != "Narrator"]

    attendees = [DAN_SPEAKER, ORCHESTRATOR_SPEAKER]
    for spk in agent_speakers:
        if spk not in attendees:
            attendees.append(spk)
    for extra in extra_attendees:
        if extra and extra not in attendees:
            attendees.append(extra)

    short_id = _short_meeting_id(meeting_id)
    attendee_str = _attendee_list(attendees)

    segments: list[Segment] = []

    # 1. Host announcement
    host_line = (
        f"This meeting is number {short_id}. Subject: {task}. "
        f"In this meeting are: {attendee_str}. Have a nice meeting."
    )
    segments.append(Segment(speaker=HOST_SPEAKER, text=host_line, role="host", pause_ms_after=450))

    # 2. Orchestrator opens
    subject_line = rng.choice(_SUBJECT_LINES).format(subject=task)
    opener = rng.choice(_ORCHESTRATOR_OPENERS).format(subject_line=subject_line)
    segments.append(Segment(speaker=ORCHESTRATOR_SPEAKER, text=opener, role="orchestrator", pause_ms_after=300))

    # 3. Agent turns with transitions
    for i, (speaker, text) in enumerate(agent_sections):
        # Handoff line from orchestrator
        if i == 0:
            handoff = rng.choice(_FIRST_HANDOFFS).format(agent=speaker)
        elif i == len(agent_sections) - 1 and len(agent_sections) > 1:
            prev = agent_sections[i - 1][0]
            handoff = rng.choice(_LAST_TRANSITIONS).format(next=speaker, prev=prev)
        else:
            prev = agent_sections[i - 1][0] if i > 0 else speaker
            handoff = rng.choice(_MID_HANDOFFS).format(prev=prev, next=speaker)
        segments.append(Segment(speaker=ORCHESTRATOR_SPEAKER, text=handoff, role="orchestrator", pause_ms_after=200))
        # Agent speaks
        segments.append(Segment(speaker=speaker, text=text, role="agent", pause_ms_after=300))

    # 4. Orchestrator closes
    segments.append(Segment(
        speaker=ORCHESTRATOR_SPEAKER,
        text=rng.choice(_ORCHESTRATOR_CLOSERS),
        role="orchestrator",
        pause_ms_after=200,
    ))

    # 5. Dan adjourns
    segments.append(Segment(
        speaker=DAN_SPEAKER,
        text=rng.choice(_DAN_CLOSERS),
        role="dan",
        pause_ms_after=0,
    ))

    return MeetingScript(
        meeting_id=short_id,
        subject=task,
        attendees=attendees,
        segments=segments,
    )


__all__ = ["Segment", "MeetingScript", "parse_report", "build_script"]
