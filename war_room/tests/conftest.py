"""Shared pytest configuration for war_room tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the `war_room` package importable as a flat layout so individual
# modules (config, state, parsing, paperclip_bridge) can be imported by
# their short names exactly the way war_room.py itself does.
WAR_ROOM_DIR = Path(__file__).resolve().parent.parent
ROOT = WAR_ROOM_DIR.parent

for path in (str(WAR_ROOM_DIR), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)
