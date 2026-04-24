"""Regression test: every /api/advertiser/{advertiser_id}/* route must call
`require_advertiser_owner` so owner-ship is enforced uniformly.

Motivated by a v0.8.x regression where 5 routes (`/special`,
`/apply-referral`, `/slides/{id}/boost`, `/slides/{id}/targeting`, and
`/fetch-webpage`) shipped without the guard and therefore honoured the path
`{advertiser_id}` without verifying the caller owned it.

This test parses the route source directly so it stays honest even if a
future contributor adds a route without wiring it into the ASGI app.
"""

from __future__ import annotations

import re
from pathlib import Path

ROUTES_FILE = Path(__file__).resolve().parents[2] / "war_room" / "dashboard" / "routes" / "advertiser.py"

_ROUTE_RE = re.compile(r"^@router\.(get|post|put|delete|patch)\(([^)]*)\)")
_FN_RE = re.compile(r"^(async\s+)?def\s+(\w+)\s*\(")
_TOP_LEVEL_RE = re.compile(r"^(async\s+)?def\s|^@router\.|^class ")


def _walk_routes() -> list[tuple[str, str, int, str]]:
    """Return (method, path, line, body) for every @router decorator that
    accepts an `{advertiser_id}` path param. Stops each function body at the
    next top-level def/@router/class."""
    lines = ROUTES_FILE.read_text(encoding="utf-8").splitlines()
    out: list[tuple[str, str, int, str]] = []
    i = 0
    while i < len(lines):
        m = _ROUTE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        method, path = m.group(1).upper(), m.group(2).strip().strip('"').strip("'")
        # Descend past stacked decorators (e.g. @router.patch + @router.post)
        j = i + 1
        while j < len(lines) and not _FN_RE.match(lines[j]):
            j += 1
        if j >= len(lines):
            break
        k = j + 1
        while k < len(lines) and not _TOP_LEVEL_RE.match(lines[k]):
            k += 1
        body = "\n".join(lines[j + 1 : k])
        if "{advertiser_id}" in path:
            out.append((method, path, j + 1, body))
        i = j
    return out


def test_every_advertiser_route_calls_require_advertiser_owner() -> None:
    """Catch any new `/api/advertiser/{advertiser_id}/*` route that forgets
    to call `require_advertiser_owner` — this is a cross-cutting auth
    invariant, not a per-route concern."""
    routes = _walk_routes()
    assert routes, "Expected to find at least one advertiser route"

    missing = [(method, path, line) for (method, path, line, body) in routes if "require_advertiser_owner" not in body]
    assert not missing, "The following advertiser routes are MISSING `require_advertiser_owner`:\n" + "\n".join(
        f"  - {m:6s} line={ln} {p}" for (m, p, ln) in missing
    )


def test_advertiser_route_count_sanity() -> None:
    """Guard against the walker accidentally matching zero routes (e.g. if
    the regex breaks after a refactor)."""
    routes = _walk_routes()
    # There were 17 at time of writing; keep the floor low so legitimate
    # additions don't trip this test.
    assert len(routes) >= 10, f"Only {len(routes)} routes found — walker may be broken"
