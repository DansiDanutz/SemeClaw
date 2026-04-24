"""`semeclaw agents` — list everything the registry knows about."""
from __future__ import annotations

import os

from . import _env
from ._ui import banner, section, row, hint, OK, MISS, INFO, _C


def run() -> int:
    _env.export_into_process()
    banner("SemeClaw agents", "Loaded from war_room/agents/ (.md files)")

    try:
        from war_room.agents import registry
    except Exception as e:
        print(f"  {_C.R}Could not load registry: {e}{_C.OFF}")
        return 1

    section(f"Core agents ({len(registry.core_agents())})")
    print(f"  {_C.DIM}These ship in the OSS demo. Customise by editing war_room/agents/<id>.md{_C.OFF}\n")
    for a in registry.core_agents():
        print(f"  {_C.BLD}{a.name}{_C.OFF}  ({a.id})")
        print(f"    {_C.DIM}role:   {a.role}{_C.OFF}")
        print(f"    {_C.DIM}models: {a.model_preference[0] if a.model_preference else '(none)'}{_C.OFF}")
        for m in a.model_preference[1:]:
            print(f"            {_C.DIM}{m}{_C.OFF}")
        print(f"    {_C.DIM}tools:  {a.tools or '(none)'}{_C.OFF}")
        print()

    section(f"Adapters ({len(registry.adapters())})")
    print(f"  {_C.DIM}Bridges to external agent systems. Set env vars to enable.{_C.OFF}\n")
    for a in registry.adapters():
        meta = a.adapter or {}
        req = meta.get("required_env", []) or []
        missing = [k for k in req if not os.environ.get(k)]
        mark = OK if not missing else MISS
        print(f"  {mark}  {_C.BLD}{a.name}{_C.OFF}  ({a.id})")
        print(f"      {_C.DIM}{a.role}{_C.OFF}")
        print(f"      {_C.DIM}protocol: {meta.get('protocol', '?')}{_C.OFF}")
        if req:
            for k in req:
                set_ = "✓" if os.environ.get(k) else "·"
                print(f"      {_C.DIM}{set_} {k}{_C.OFF}")
        print()

    hint("Add a new agent: drop a .md file with frontmatter into war_room/agents/")
    hint("Add a new adapter: drop a .md file into war_room/agents/adapters/")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
