"""`semeclaw doctor` — actionable connectivity + smoke-test diagnostics.

`status` shows what's CONFIGURED. `doctor` actually CALLS each service and
reports whether it works end-to-end. Designed to be the single command an
AI coding agent (Claude Code / Codex) runs after clone to know what to fix.

Exit code:
  0  everything green or only optional things missing
  1  something required is broken
  2  config not loaded (run `semeclaw setup`)

Output is also valid YAML on the right side of each row, so an AI agent
can grep `[!!]` lines and act on them autonomously.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error

from . import _env
from ._ui import OK, MISS, ERR, INFO, banner, section, row, hint, success, fail


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: float = 6.0, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(512).decode("utf-8", "replace") if hasattr(e, "read") else ""
    except Exception as e:
        return 0, str(e)


def _http_post(url: str, body: dict, timeout: float = 8.0,
               headers: dict | None = None) -> tuple[int, str]:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(512).decode("utf-8", "replace") if hasattr(e, "read") else ""
    except Exception as e:
        return 0, str(e)


def _api_base() -> str:
    return (os.environ.get("SEMECLAW_API")
            or os.environ.get("SEMECLAW_PUBLIC_URL")
            or "http://127.0.0.1:8765").rstrip("/")


def _probe_dashboard() -> tuple[bool, str]:
    # /api/agents is always-on (no auth, no DB needed). Try /version.json first
    # for richer info, fall back to /api/agents for liveness.
    code, body = _http_get(_api_base() + "/version.json", timeout=3.0)
    if code == 200:
        try:
            v = json.loads(body).get("version", "?")
            return True, f"online v{v}"
        except Exception:
            return True, "online"
    code2, _ = _http_get(_api_base() + "/api/agents", timeout=3.0)
    if code2 == 200:
        return True, "online (no version.json)"
    if code == 0 and code2 == 0:
        return False, f"unreachable ({body[:80]})"
    return False, f"HTTP {code or code2}"


def _probe_supabase_tasks_table() -> tuple[bool, str]:
    code, body = _http_get(_api_base() + "/api/tasks/quota", timeout=6.0)
    if code != 200:
        return False, f"HTTP {code}"
    try:
        j = json.loads(body)
        if not j.get("ok"):
            return False, j.get("error", "unknown")
        q = j.get("quota", {})
        return True, f"active={q.get('active', 0)}/{q.get('cap', 100)}"
    except Exception as e:
        return False, str(e)


def _probe_openrouter() -> tuple[bool, str]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return False, "OPENROUTER_API_KEY not set"
    code, body = _http_get("https://openrouter.ai/api/v1/auth/key", timeout=8.0,
                           headers={"Authorization": f"Bearer {key}"})
    if code == 200:
        try:
            j = json.loads(body)
            label = j.get("data", {}).get("label") or "key valid"
            return True, label
        except Exception:
            return True, "key valid"
    if code in (401, 403):
        return False, "key rejected — regenerate at openrouter.ai/keys"
    return False, f"HTTP {code}"


def _probe_duckduckgo() -> tuple[bool, str]:
    code, _ = _http_get("https://html.duckduckgo.com/", timeout=5.0)
    return (code in (200, 202, 301, 302)), f"HTTP {code or 'no-conn'}"


def _probe_dns() -> tuple[bool, str]:
    try:
        socket.gethostbyname("openrouter.ai")
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _probe_adapters() -> list[tuple[str, bool, str]]:
    """Return [(name, ready, detail)] for each adapter."""
    out = []
    pp_base = os.environ.get("PAPERCLIP_BASE_URL", "").strip()
    pp_key  = os.environ.get("PAPERCLIP_API_KEY", "").strip()
    out.append(("paperclip", bool(pp_base and pp_key),
                "ready" if (pp_base and pp_key) else "set PAPERCLIP_BASE_URL + PAPERCLIP_API_KEY"))
    mo_base = os.environ.get("MOLTICA_BASE_URL", "").strip()
    mo_key  = os.environ.get("MOLTICA_API_KEY", "").strip()
    out.append(("moltica", bool(mo_base and mo_key),
                "ready" if (mo_base and mo_key) else "set MOLTICA_BASE_URL + MOLTICA_API_KEY"))
    from pathlib import Path
    cc = (Path.home() / ".claude" / "projects").exists()
    out.append(("claude_code", cc, "scanning ~/.claude/projects" if cc else "no ~/.claude/projects (optional)"))
    inbox = __import__("pathlib").Path(__file__).resolve().parent.parent / "war_room" / "tasks" / "inbox"
    n = len(list(inbox.glob("*.json"))) if inbox.exists() else 0
    out.append(("local-files", True, f"{n} file(s) in {inbox}"))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[2:])
    as_json = "--json" in args

    _env.export_into_process()

    # ---------- collect results ----------
    results: dict = {"ok": True, "checks": [], "hard_fail": False}

    def record(group: str, name: str, ok: bool, detail: str, hint_text: str = "",
               required: bool = False) -> None:
        results["checks"].append({
            "group": group, "name": name, "ok": ok,
            "detail": detail, "hint": hint_text, "required": required,
        })
        if required and not ok:
            results["hard_fail"] = True
            results["ok"] = False

    ok, msg = _probe_dns()
    record("network", "DNS resolution", ok, msg, required=True)

    ok, msg = _probe_dashboard()
    record("dashboard", f"GET {_api_base()}", ok, msg,
           "Start it: semeclaw war-room  (in another terminal)")

    ok, msg = _probe_supabase_tasks_table()
    record("supabase", "GET /api/tasks/quota", ok, msg,
           "Apply migration war_room/db/migrations/2026_04_24_semeclaw_tasks.sql; "
           "set DLS_TEAM_SUPABASE_URL + DLS_TEAM_SUPABASE_SERVICE_KEY")

    ok, msg = _probe_openrouter()
    record("llm", "OpenRouter /auth/key", ok, msg,
           "Free key at https://openrouter.ai/keys; without it, dialog uses templates")

    ok, msg = _probe_duckduckgo()
    record("search", "DuckDuckGo (default backend)", ok, msg)

    for name, ready, detail in _probe_adapters():
        record("adapters", name, ready, detail,
               f"set the listed env vars in .env then re-run")

    # ---------- output ----------
    if as_json:
        print(json.dumps(results, indent=2))
        return 1 if results["hard_fail"] else 0

    banner("semeclaw doctor", "End-to-end connectivity + smoke tests")

    last_group = ""
    for chk in results["checks"]:
        if chk["group"] != last_group:
            section(chk["group"].title())
            last_group = chk["group"]
        icon = OK if chk["ok"] else (ERR if chk["required"] else MISS)
        row(icon, chk["name"], chk["detail"])
        if not chk["ok"] and chk["hint"]:
            hint(chk["hint"])

    print()
    if results["hard_fail"]:
        fail("doctor found a HARD failure — fix the [!!] lines first")
        return 1
    success("doctor: no blocking issues")
    print("  Next: semeclaw tasks sync && semeclaw tasks list")
    print("  AI agents: re-run as `semeclaw doctor --json` for machine-readable output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
