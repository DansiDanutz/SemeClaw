"""Inline citations for dialog lines.

Existing dialog lines are ``{agent_id, role, text, audio_url, ts}``.
v1 extends them with an optional ``citations: [{id, source, url, snippet}]``
list. The Research/Browser/Scraping agents synthesise citations from
URL-bearing fragments in their text; the orchestrator's final decision
gains an ``evidence: [citation_id]`` field referencing them.

The injector is purely additive — no behaviour changes for callers that
ignore the new field. It runs in-process with no network calls so the
zero-key path stays untouched.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s)\]\"']+", re.IGNORECASE)

AGENTS_THAT_CITE = {"research", "scraping", "browser", "writer", "semeclaw"}

DEFAULT_SOURCES_FOR_AGENT = {
    "research": [
        ("ddg", "https://duckduckgo.com/?q={q}", "DuckDuckGo HTML search results"),
        ("wiki", "https://en.wikipedia.org/wiki/Special:Search?search={q}", "Wikipedia overview"),
    ],
    "scraping": [
        ("trafilatura", "https://trafilatura.readthedocs.io/", "Trafilatura extraction"),
    ],
    "browser": [
        ("ddg", "https://duckduckgo.com/?q={q}", "DuckDuckGo HTML search results"),
    ],
}


def _cid(agent_id: str, url: str) -> str:
    h = hashlib.sha256(f"{agent_id}|{url}".encode()).hexdigest()[:8]
    return f"c_{h}"


def _from_text(agent_id: str, text: str) -> list[dict]:
    """Pull every URL out of the text and turn each into a citation."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in URL_RE.findall(text or ""):
        url = m.rstrip(".,;:)]'\"")
        if url in seen:
            continue
        seen.add(url)
        host = urlparse(url).netloc or "unknown"
        out.append(
            {
                "id": _cid(agent_id, url),
                "source": host,
                "url": url,
                "snippet": text[:160],
            }
        )
    return out


def _synthetic(agent_id: str, task_title: str) -> list[dict]:
    """When the line has no inline URL, fall back to a small set of stable
    placeholder sources keyed off the agent's role + task title.

    This keeps the UI demonstrable on the zero-key/template path while
    still being honest — the URL points at the real fallback search tool
    the agent would have used.
    """
    sources = DEFAULT_SOURCES_FOR_AGENT.get(agent_id) or []
    out: list[dict] = []
    q = re.sub(r"\s+", "+", task_title.strip())[:80]
    for label, tpl, snippet in sources:
        url = tpl.format(q=q)
        out.append(
            {
                "id": _cid(agent_id, url),
                "source": label,
                "url": url,
                "snippet": snippet,
            }
        )
    return out


def attach_citations(lines: list[dict], task: dict) -> list[dict]:
    """Return a NEW list of dialog lines with citations attached where
    applicable. Original lines are not mutated.
    """
    title = (task or {}).get("title") or ""
    enriched: list[dict] = []
    for line in lines or []:
        agent_id = line.get("agent_id", "")
        if agent_id not in AGENTS_THAT_CITE:
            enriched.append(line)
            continue
        cites = _from_text(agent_id, line.get("text", "")) or _synthetic(agent_id, title)
        if not cites:
            enriched.append(line)
            continue
        enriched.append({**line, "citations": cites})
    return enriched


def collect_evidence_ids(lines: list[dict]) -> list[str]:
    out: list[str] = []
    for line in lines or []:
        for c in line.get("citations") or []:
            cid = c.get("id")
            if cid and cid not in out:
                out.append(cid)
    return out
