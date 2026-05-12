"""Edge-case coverage for v1 modules — trailing punctuation in URLs,
audit middleware body handling, citation evidence collection, and the
intervene early-commit code path.
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# Citations URL extraction
# ---------------------------------------------------------------------------
def test_citations_strip_trailing_punctuation():
    """URLs in markdown often end at ``.`` or ``,`` — those must not be
    treated as part of the link."""
    from war_room.v1.citations import attach_citations

    lines = [
        {
            "agent_id": "research",
            "role": "Researcher",
            "text": "Refer to https://example.com/page. Then check (https://wiki.example.org/a), or https://acme.test/foo],",
        }
    ]
    enriched = attach_citations(lines, {"title": "demo"})
    urls = sorted(c["url"] for c in enriched[0]["citations"])
    # Trailing dot/comma/paren/bracket must be stripped from each URL.
    assert urls == [
        "https://acme.test/foo",
        "https://example.com/page",
        "https://wiki.example.org/a",
    ]


def test_citations_dedupes_repeated_urls():
    from war_room.v1.citations import attach_citations

    line = {
        "agent_id": "research",
        "role": "R",
        "text": "https://x.test and again https://x.test and one more https://x.test",
    }
    enriched = attach_citations([line], {"title": "demo"})
    assert len(enriched[0]["citations"]) == 1


def test_collect_evidence_ids_dedupes_across_lines():
    """Citations are keyed by (agent_id, url) so the same URL surfaced by
    two different agents counts as two pieces of evidence (provenance is
    preserved), but the same agent quoting the same URL twice is one.
    """
    from war_room.v1.citations import attach_citations, collect_evidence_ids

    lines = [
        {"agent_id": "research", "role": "R", "text": "https://a.test and https://a.test"},
        {"agent_id": "writer", "role": "W", "text": "I'll draft."},
        {"agent_id": "browser", "role": "B", "text": "https://b.test and https://a.test"},
    ]
    enriched = attach_citations(lines, {"title": "x"})
    ids = collect_evidence_ids(enriched)
    # research:a.test, browser:b.test, browser:a.test — 3 distinct citations.
    assert len(ids) == 3
    # The two `a.test` citations have different IDs because they belong
    # to different agents.
    assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# Convergence — edge thresholds
# ---------------------------------------------------------------------------
def test_convergence_empty_comment_returns_neutral():
    from war_room.v1.convergence import score_convergence

    s = score_convergence(turn=2, new_comment="   ", prior_comments=["x"], prior_replies=[])
    assert s.score == 0.0
    assert s.reason == "empty_comment"
    assert s.early_commit is False


def test_convergence_objection_blocks_even_with_overlap():
    """If the new comment has both 'agree' and 'but', the disagreement
    must keep the gate closed."""
    from war_room.v1.convergence import score_convergence

    s = score_convergence(
        turn=3,
        new_comment="agree but wait — there's a concern about latency",
        prior_comments=["proceed"],
        prior_replies=["sounds good"],
    )
    assert s.early_commit is False


# ---------------------------------------------------------------------------
# Audit middleware
# ---------------------------------------------------------------------------
def test_audit_middleware_logs_writes_only(v1_data_dir):
    from war_room.v1 import audit, storage

    app = FastAPI()
    audit.install(app)

    @app.get("/api/read")
    async def read():
        return {"ok": True}

    @app.post("/api/write")
    async def write(payload: dict | None = None):  # noqa: ARG001
        return {"ok": True}

    with TestClient(app) as c:
        c.get("/api/read")  # should NOT be audited
        c.post("/api/write", json={"x": 1})
        c.post("/api/write", json={"x": 2})

    # The middleware fires-and-forgets; drain the loop briefly so the
    # background _persist tasks finish before we inspect the log.
    time.sleep(0.1)
    rows = storage.query_audit(limit=50)
    methods = [r["method"] for r in rows]
    routes = sorted(r["route"] for r in rows)
    assert methods.count("POST") == 2
    assert "GET" not in methods
    assert routes == ["/api/write", "/api/write"]


def test_audit_middleware_excludes_streaming_paths(v1_data_dir):
    from war_room.v1 import audit, storage

    app = FastAPI()
    audit.install(app)

    @app.post("/api/tts")
    async def tts():
        return {"ok": True}

    @app.post("/ws/stream")
    async def ws():
        return {"ok": True}

    with TestClient(app) as c:
        c.post("/api/tts")
        c.post("/ws/stream")

    time.sleep(0.1)
    rows = storage.query_audit(limit=10)
    assert rows == []


def test_audit_middleware_hashes_body_not_raw(v1_data_dir):
    from war_room.v1 import audit, storage

    app = FastAPI()
    audit.install(app)

    @app.post("/api/echo")
    async def echo(payload: dict | None = None):
        return {"got": payload}

    raw = b'{"secret":"super-sensitive-token"}'
    with TestClient(app) as c:
        c.post("/api/echo", content=raw, headers={"content-type": "application/json"})

    time.sleep(0.1)
    rows = storage.query_audit(limit=10)
    assert len(rows) == 1
    entry = rows[0]
    # The hash must be present and reflect the body but the raw body must NOT be.
    assert entry["request_hash"]
    serialized = json.dumps(entry)
    assert "super-sensitive-token" not in serialized
    expected = hashlib.sha256(raw).hexdigest()[:16]
    assert entry["request_hash"] == expected


# ---------------------------------------------------------------------------
# Convergence payload shape — guards the dashboard's reader contract
# ---------------------------------------------------------------------------
def test_convergence_signal_exposes_dashboard_contract():
    """The dashboard reads ``score``, ``agreement``, ``overlap``,
    ``early_commit``, and ``reason``. The dataclass surface must stay
    stable so a payload change isn't silently breaking the SPA.
    """
    from war_room.v1 import convergence as _conv

    s = _conv.score_convergence(
        turn=2,
        new_comment="lgtm agreed proceed approved",
        prior_comments=["sounds good"],
        prior_replies=["ship it"],
    )
    payload = {
        "score": s.score,
        "agreement": s.agreement,
        "overlap": s.overlap,
        "early_commit": s.early_commit,
        "reason": s.reason,
    }
    assert isinstance(payload["score"], float)
    assert isinstance(payload["agreement"], float)
    assert isinstance(payload["overlap"], float)
    assert isinstance(payload["early_commit"], bool)
    assert isinstance(payload["reason"], str)
