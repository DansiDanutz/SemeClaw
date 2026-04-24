"""`semeclaw tasks` — sync, list, dialog, quota.

Hits a running War Room dashboard server. Defaults to
http://127.0.0.1:8765 — override with SEMECLAW_API or `--api` flag.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

from cli._ui import banner, section, row, OK, ERR, INFO, MISS, hint, success, fail


def _api_base() -> str:
    return (os.environ.get("SEMECLAW_API")
            or os.environ.get("SEMECLAW_PUBLIC_URL")
            or "http://127.0.0.1:8765").rstrip("/")


def _http(method: str, path: str, body: dict | None = None) -> dict:
    url = _api_base() + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _short(s: str, n: int = 60) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_sync() -> int:
    banner("semeclaw tasks sync", "Pull tasks from every configured adapter")
    res = _http("POST", "/api/tasks/sync")
    if not res.get("ok"):
        fail(f"sync failed: {res.get('error')}")
        return 1
    section("Result")
    row(OK, "tenant", res.get("tenant_id", ""))
    row(OK, "synced", str(res.get("synced", 0)))
    for src, n in (res.get("by_source") or {}).items():
        row(INFO, f"  from {src}", str(n))
    success("done")
    return 0


def cmd_list() -> int:
    banner("semeclaw tasks list", _api_base())
    res = _http("GET", "/api/tasks?limit=50")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    tasks = res.get("tasks", []) or []
    if not tasks:
        hint("no tasks yet — run `semeclaw tasks sync` first")
        return 0
    section(f"{len(tasks)} task(s)")
    for t in tasks:
        ic = OK if t["status"] == "done" else (MISS if t["status"] == "open" else INFO)
        row(ic, _short(t["title"], 40), f'{t["source"]}/{t["status"]}  id={t["id"][:8]}')
    return 0


def cmd_dialog(task_id: str | None) -> int:
    if not task_id:
        fail("usage: semeclaw tasks dialog <task_id>")
        return 2
    banner("semeclaw tasks dialog", task_id)
    res = _http("GET", f"/api/tasks/{task_id}/dialog")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    d = res.get("dialog", {}) or {}
    section(f"v{d.get('version', '?')}  ({'newly generated' if res.get('generated') else 'cached'})")
    for line in d.get("lines", []) or []:
        print(f"  [{line.get('agent_id'):>10}]  {line.get('text')}")
    return 0


def cmd_quota() -> int:
    banner("semeclaw tasks quota", _api_base())
    res = _http("GET", "/api/tasks/quota")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    q = res.get("quota", {}) or {}
    section("Quota")
    row(OK, "tenant", q.get("tenant_id", ""))
    row(OK, "active", f"{q.get('active', 0)} / {q.get('cap', 100)}")
    row(INFO, "archived", str(q.get("archived", 0)))
    row(INFO, "available", str(q.get("available", 0)))
    if res.get("over_cap"):
        section("Over cap — suggestions")
        for s in res.get("suggestions", []) or []:
            hint(s)
    return 0


def cmd_gc() -> int:
    banner("semeclaw tasks gc", "Enforce 100-task cap (archives oldest)")
    res = _http("POST", "/api/tasks/gc")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    print(json.dumps(res.get("result", {}), indent=2))
    return 0


USAGE = """\
semeclaw tasks <command>

Commands:
  sync                Pull from every configured adapter
  list                List most-recent tasks
  dialog <task_id>    Show (or auto-generate) the meeting-room dialog
  quota               Show retention quota + over-cap suggestions
  gc                  Enforce the 100-task cap right now
"""


def run(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[2:])  # skip "semeclaw tasks"
    cmd = (args[0] if args else "").lower()
    if cmd == "sync":   return cmd_sync()
    if cmd == "list":   return cmd_list()
    if cmd == "dialog": return cmd_dialog(args[1] if len(args) > 1 else None)
    if cmd == "quota":  return cmd_quota()
    if cmd == "gc":     return cmd_gc()
    print(USAGE)
    return 0 if cmd in ("", "help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(run())
