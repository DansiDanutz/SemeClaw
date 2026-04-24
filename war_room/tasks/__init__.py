"""SemeClaw task ingest, dialog generation, and retention.

Phase A: read-only foundation.
- _db        Supabase REST helper (mirrors war_room.dashboard.routes.advertiser._supa).
- sources    Pluggable task discovery: paperclip / moltica / local files / claude_code.
- dialog     Per-agent line generation + weave into a meeting-room script.
- retention  100-task soft cap with archival + over-cap suggestions.
"""
from __future__ import annotations

from . import _db, sources, dialog, retention  # noqa: F401
