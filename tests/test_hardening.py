from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "war_room" / "dashboard"))

import server as srv

from semeclaw.tools import builtin_tools


def test_watch_token_roundtrip_legacy_helper() -> None:
    token = srv._issue_watch_token("127.0.0.1")
    assert srv._verify_watch_token(token, "127.0.0.1") is True


def test_bash_runs_simple_command() -> None:
    output = asyncio.run(
        builtin_tools.bash.execute({"command": "python3 -c \"print('ok')\""}, session=None)
    )
    assert output.strip() == "ok"


def test_bash_rejects_shell_metacharacters() -> None:
    output = asyncio.run(
        builtin_tools.bash.execute({"command": "python3 -c \"print('ok')\"; echo hacked"}, session=None)
    )
    assert "unsafe shell syntax" in output.lower()
