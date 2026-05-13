"""`semeclaw status` — checklist of what's configured and what's missing."""
from __future__ import annotations

import os
import shutil

from . import _env
from ._ui import _C, ERR, INFO, MISS, OK, banner, hint, row, section


def _check_python() -> tuple[bool, str]:
    import sys
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}"


def _check_dep(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _check_ollama() -> tuple[bool, str]:
    if not shutil.which("ollama"):
        return False, "not on PATH"
    try:
        import subprocess
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=4)
        if r.returncode != 0:
            return False, "ollama daemon not running"
        lines = [ln for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("NAME")]
        return True, f"{len(lines)} model(s) installed"
    except Exception as e:
        return False, str(e)


def _check_openrouter() -> tuple[bool, str]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return False, "OPENROUTER_API_KEY not set"
    return True, f"key set ({len(key)} chars)"


def _check_search() -> dict:
    try:
        from war_room.agents import browser_search
        return browser_search.status()
    except Exception:
        return {"duckduckgo": True}


def run() -> int:
    _env.export_into_process()
    banner("SemeClaw status",
           "Everything that needs to be in place for the live demo")

    section("System")
    py_ok, py_ver = _check_python()
    row(OK if py_ok else ERR, "Python >= 3.10", py_ver)
    for dep, label in [("httpx", "httpx"), ("yaml", "PyYAML"), ("fastapi", "FastAPI")]:
        ok = _check_dep(dep)
        row(OK if ok else MISS, label, "installed" if ok else "pip install -r requirements.txt")

    section("LLM providers")
    or_ok, or_msg = _check_openrouter()
    row(OK if or_ok else MISS, "OpenRouter (free models)", or_msg)
    if not or_ok:
        hint("Get a free key at https://openrouter.ai/keys, then run: semeclaw setup")
    ol_ok, ol_msg = _check_ollama()
    row(OK if ol_ok else MISS, "Ollama (local fallback)", ol_msg)
    if not ol_ok:
        hint("Install: https://ollama.com/download — fully optional, free models work without it")

    section("Web tools")
    s = _check_search()
    row(OK if s.get("brave") else MISS, "Brave Search", "BRAVE_SEARCH_API_KEY set" if s.get("brave") else "free 2k/mo at https://brave.com/search/api/")
    row(OK if s.get("searxng") else MISS, "SearXNG", "SEARXNG_URL set" if s.get("searxng") else "optional self-hosted")
    row(OK, "DuckDuckGo (default)", "no key required")

    section("Agents")
    try:
        from war_room.agents import registry
        core = registry.core_agents()
        adapters = registry.adapters()
        row(OK if len(core) >= 4 else MISS, "Core agents loaded", f"{len(core)} agents")
        for a in core:
            print(f"      - {a.name:20} {_C.DIM}models={len(a.model_preference)}  tools={a.tools}{_C.OFF}")
        row(INFO, "Adapters available", f"{len(adapters)} (set env vars to enable)")
        for a in adapters:
            req = (a.adapter or {}).get("required_env", []) or []
            missing = [k for k in req if not os.environ.get(k)]
            mark = OK if not missing else MISS
            print(f"      {mark}  {a.name:20} {_C.DIM}{'ready' if not missing else 'missing: ' + ', '.join(missing)}{_C.OFF}")
    except Exception as e:
        row(ERR, "Agent registry", str(e))

    section("Demo")
    from pathlib import Path
    demo_path = Path(__file__).resolve().parent.parent / "demo" / "tasks" / "four-agent-live-demo.md"
    row(OK if demo_path.exists() else MISS, "Saved demo task", str(demo_path) if demo_path.exists() else "missing")
    if demo_path.exists():
        hint("Run it: semeclaw demo")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
