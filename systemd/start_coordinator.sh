#!/bin/bash
cd /Users/davidai/SemeClaw
source ~/.openclaw/fleet.env 2>/dev/null || true
exec /opt/homebrew/bin/uv run python3 -m coordinator.coordinator
