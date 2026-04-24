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


def cmd_list(as_json: bool = False) -> int:
    res = _http("GET", "/api/tasks?limit=50")
    if as_json:
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    banner("semeclaw tasks list", _api_base())
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


def cmd_comment(task_id: str | None, comment: str | None, as_json: bool = False) -> int:
    if not task_id or not comment:
        fail("usage: semeclaw tasks comment <task_id> <\"comment text\">")
        return 2
    res = _http("POST", f"/api/tasks/{task_id}/intervene", {"comment": comment})
    if as_json:
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    banner("semeclaw tasks comment", f"task={task_id[:8]}  turn={res.get('turn_index','?')}/3")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    section("Agent replies")
    for r in res.get("agent_replies", []) or []:
        print(f"  [{r.get('agent_id'):>10}] {r.get('text')}")
    if res.get("orchestrator_decision"):
        section("Orchestrator decision (intervention #3)")
        d = res["orchestrator_decision"]
        print(f"  rationale: {d.get('rationale','')}")
        patch = d.get("task_patch", {}) or {}
        for k, v in patch.items():
            print(f"  patch.{k}: {v}")
        if res.get("new_dialog"):
            print(f"  new dialog v{res['new_dialog'].get('version')} composed")
        wb = res.get("writeback") or {}
        if wb:
            print(f"  writeback: {wb}")
    return 0


def cmd_interventions(task_id: str | None) -> int:
    if not task_id:
        fail("usage: semeclaw tasks interventions <task_id>")
        return 2
    res = _http("GET", f"/api/tasks/{task_id}/interventions")
    if not res.get("ok"):
        fail(res.get("error", "unknown"))
        return 1
    banner("semeclaw tasks interventions",
           f"dialog v{res.get('version')}  count={res.get('count', 0)}/3")
    for iv in res.get("interventions", []) or []:
        section(f"turn {iv.get('turn_index')}")
        print(f"  user: {iv.get('user_comment')}")
        for r in iv.get("agent_replies", []) or []:
            print(f"  [{r.get('agent_id'):>10}] {r.get('text')}")
        if iv.get("orchestrator_decision"):
            print(f"  orchestrator: {iv['orchestrator_decision'].get('rationale','')}")
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
  comment <id> "..."  Add a user comment (intervention). Turn 3 triggers orchestrator.
  interventions <id>  Show all interventions on the latest dialog
  quota               Show retention quota + over-cap suggestions
  gc                  Enforce the 100-task cap right now

Flags:
  --json              Machine-readable output (sync, list, dialog, comment)
"""


def run(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[2:])  # skip "semeclaw tasks"
    cmd = (args[0] if args else "").lower()
    if cmd == "sync":   return cmd_sync()
    if cmd == "list":   return cmd_list(as_json=("--json" in args))
    if cmd == "dialog": return cmd_dialog(args[1] if len(args) > 1 else None)
    if cmd == "quota":  return cmd_quota()
    if cmd == "gc":     return cmd_gc()
    if cmd == "comment":
        return cmd_comment(args[1] if len(args) > 1 else None,
                           args[2] if len(args) > 2 else None,
                           as_json=("--json" in args))
    if cmd == "interventions":
        return cmd_interventions(args[1] if len(args) > 1 else None)
    print(USAGE)
    return 0 if cmd in ("", "help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(run())
