"""
War Room — Writer output parsing

The Writer agent emits markdown. We extract:
  - title (first H1, "Title:" line, or first non-blank line)
  - description (prose up to first AC/checklist section)
  - acceptance criteria (all `- [ ]` bullets, anywhere in the doc)

Kept intentionally liberal so the parser is a best-effort guide, never a
blocker. If the Writer ignores the format, we fall back to the raw task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$")
_TITLE_LINE_RE = re.compile(r"^\s*title\s*:\s*(.+?)\s*$", re.IGNORECASE)
_AC_HEADER_RE = re.compile(r"^\s*#{1,6}\s*acceptance\s+criteria\b", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[\s*\]\s*(.+?)\s*$")
_MAX_TITLE_LEN = 140
_MAX_DESC_LEN = 2000


@dataclass(frozen=True)
class WriterOutput:
    title: str
    description: str
    acceptance_criteria: tuple[str, ...]

    def as_tuple(self) -> tuple[str, str, list[str]]:
        """Compatibility shape for the original `_parse_writer_output`."""
        return self.title, self.description, list(self.acceptance_criteria)


def parse_writer_output(text: str, fallback_task: str) -> WriterOutput:
    """Extract issue title / description / acceptance criteria from Writer output."""
    raw = (text or "").strip()
    if not raw:
        return WriterOutput(
            title=_truncate_title(f"[War Room] {fallback_task}"),
            description=fallback_task,
            acceptance_criteria=(),
        )

    lines = raw.splitlines()

    title = _extract_title(lines) or f"[War Room] {fallback_task}"
    title = _truncate_title(title)

    ac = tuple(_extract_acceptance_criteria(lines))
    description = _extract_description(lines)
    if not description:
        description = raw
    description = description[:_MAX_DESC_LEN]

    return WriterOutput(title=title, description=description, acceptance_criteria=ac)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_title(lines: list[str]) -> str | None:
    for line in lines:
        m = _TITLE_LINE_RE.match(line)
        if m:
            return m.group(1)
    for line in lines:
        m = _H1_RE.match(line)
        if m:
            return m.group(1)
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _extract_acceptance_criteria(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        m = _CHECKBOX_RE.match(line)
        if m:
            item = m.group(1).strip()
            if item and item not in items:
                items.append(item)
    return items


def _extract_description(lines: list[str]) -> str:
    """Pull prose before the first Acceptance Criteria heading / checklist run."""
    cut = len(lines)
    for idx, line in enumerate(lines):
        if _AC_HEADER_RE.match(line):
            cut = idx
            break
        if _CHECKBOX_RE.match(line):
            cut = idx
            break
    body = "\n".join(lines[:cut])
    # Drop the title line if it's the first H1 — callers already have it.
    body_lines = body.splitlines()
    if body_lines and _H1_RE.match(body_lines[0]):
        body_lines = body_lines[1:]
    return "\n".join(body_lines).strip()


def _truncate_title(title: str) -> str:
    title = title.strip().strip("#").strip()
    if len(title) <= _MAX_TITLE_LEN:
        return title
    return title[: _MAX_TITLE_LEN - 1].rstrip() + "…"
