"""Wire all v1 features onto an existing FastAPI app.

Public surface:
  - GET    /api/v1/about
  - POST   /api/tenants
  - GET    /api/tenants
  - GET    /api/tenants/{tid}
  - DELETE /api/tenants/{tid}
  - PATCH  /api/tenants/{tid}/plan
  - GET    /api/admin/audit
  - GET    /api/admin/dlq/{name}/replay-status
  - POST   /api/admin/dlq/{name}/replay
  - GET    /api/admin/v1/overview
  - GET    /api/v1/adapters
  - POST   /api/v1/adapters/discord/sync   (proof of pattern)
  - POST   /api/v1/dialog/preview          (citations + convergence demo)
  - GET    /admin                          (vanilla-JS SPA)
  - GET    /admin/static/{file}            (admin SPA assets)

Every write endpoint that returns data also surfaces through the audit
middleware installed by ``war_room.v1.audit.install``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from fastapi.responses import StreamingResponse

from war_room.v1 import adapters as _adapters
from war_room.v1 import audit as _audit
from war_room.v1 import citations as _cites
from war_room.v1 import convergence as _conv
from war_room.v1 import dlq_replay as _replay
from war_room.v1 import exports as _exports
from war_room.v1 import spotlight as _spotlight
from war_room.v1 import sse as _sse
from war_room.v1 import storage as _s
from war_room.v1 import tenants as _tenants
from war_room.v1 import usage as _usage
from war_room.v1 import V1_VERSION

logger = logging.getLogger("semeclaw.v1")

ADMIN_KEY_ENV = "SEMECLAW_ADMIN_KEY"
ADMIN_STATIC_DIR = Path(__file__).parent / "static" / "admin"


def _admin_key() -> str:
    return (os.environ.get(ADMIN_KEY_ENV) or os.environ.get("SEMECLAW_API_KEY") or "").strip()


def _require_admin(request: Request) -> None:
    """Lightweight admin gate. Falls back to SEMECLAW_API_KEY (which the
    existing _semeclaw_admin_gate already enforces on /api/admin/*) so
    routes mounted under /admin or /api/v1 can opt in here."""
    key = _admin_key()
    if not key:
        raise HTTPException(status_code=503, detail=f"set {ADMIN_KEY_ENV} (or SEMECLAW_API_KEY) to enable admin routes")
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {key}":
        raise HTTPException(status_code=401, detail="unauthorized")


def register_v1(app: FastAPI) -> None:
    """Mount every v1 feature onto the given app. Idempotent — safe to call
    once at module import time. Returns nothing."""
    if getattr(app.state, "_semeclaw_v1_registered", False):  # pragma: no cover
        return
    app.state._semeclaw_v1_registered = True
    _audit.install(app)
    _register_about(app)
    _register_tenants(app)
    _register_admin_routes(app)
    _register_adapters(app)
    _register_dialog_preview(app)
    _register_spotlight(app)
    _register_usage(app)
    _register_sse(app)
    _register_exports(app)
    _register_admin_spa(app)


# ---------------------------------------------------------------------------
# /api/v1/about — manifest of v1 features
# ---------------------------------------------------------------------------
def _register_about(app: FastAPI) -> None:
    @app.get("/api/v1/about")
    async def v1_about():
        return JSONResponse(
            {
                "version": V1_VERSION,
                "features": [
                    "tenants_crud",
                    "audit_log",
                    "audit_csv_export",
                    "audit_retention",
                    "dlq_replay",
                    "convergence_scoring",
                    "inline_citations",
                    "discord_adapter",
                    "linear_adapter",
                    "notion_adapter",
                    "jira_adapter",
                    "spotlight_rotation",
                    "ad_serve",
                    "per_tenant_usage",
                    "quota_check",
                    "sse_live_admin",
                    "admin_dashboard_tabs",
                ],
                "endpoints": {
                    "tenants": [
                        "POST /api/tenants",
                        "GET /api/tenants",
                        "DELETE /api/tenants/{id}",
                        "PATCH /api/tenants/{id}/plan",
                        "GET /api/tenants/{id}/usage",
                    ],
                    "audit": [
                        "GET /api/admin/audit",
                        "GET /api/admin/audit.csv",
                        "POST /api/admin/audit/gc",
                    ],
                    "dlq_replay": [
                        "POST /api/admin/dlq/{name}/replay",
                        "GET /api/admin/dlq/{name}/replay-status",
                    ],
                    "adapters": [
                        "GET /api/v1/adapters",
                        "POST /api/v1/adapters/{id}/sync",
                    ],
                    "spotlight": [
                        "GET /api/v1/spotlight",
                        "GET /spotlight",
                        "POST /api/v1/spotlight/overrides",
                        "DELETE /api/v1/spotlight/overrides/{id}",
                        "GET /api/v1/ads/next",
                    ],
                    "usage": ["GET /api/admin/v1/usage"],
                    "events": ["GET /api/admin/v1/events"],
                    "dialog_preview": ["POST /api/v1/dialog/preview"],
                    "admin": ["GET /admin"],
                },
                "data_dir": str(_s.V1_DATA_DIR.resolve()),
                "spotlight_source": str(_spotlight.source_path()),
            }
        )


# ---------------------------------------------------------------------------
# Tenants CRUD
# ---------------------------------------------------------------------------
def _tenant_view(t: _tenants.Tenant, *, include_secret: str | None = None) -> dict:
    out = asdict(t)
    out["plan_limits"] = _tenants.plan_limits(t.plan)
    if include_secret:
        out["api_key"] = include_secret
        out["api_key_warning"] = "Store this value now — it is not retrievable later."
    return out


def _register_tenants(app: FastAPI) -> None:
    @app.post("/api/tenants")
    async def tenants_create(payload: dict = Body(default_factory=dict)):
        name = (payload or {}).get("name") or ""
        plan = (payload or {}).get("plan") or "free"
        if not name.strip():
            return JSONResponse({"error": "name is required"}, status_code=400)
        try:
            tenant, plain_key = await _tenants.create_tenant(name=name, plan=plan)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(_tenant_view(tenant, include_secret=plain_key), status_code=201)

    @app.get("/api/tenants")
    async def tenants_list(include_deleted: bool = False):
        rows = await _tenants.list_tenants(include_deleted=include_deleted)
        return JSONResponse({"tenants": [_tenant_view(t) for t in rows], "total": len(rows)})

    @app.get("/api/tenants/{tid}")
    async def tenants_get(tid: str):
        t = await _tenants.get_tenant(tid)
        if t is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_tenant_view(t))

    @app.delete("/api/tenants/{tid}")
    async def tenants_delete(tid: str):
        t = await _tenants.soft_delete(tid)
        if t is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"deleted": True, "tenant": _tenant_view(t)})

    @app.patch("/api/tenants/{tid}/plan")
    async def tenants_update_plan(tid: str, payload: dict = Body(default_factory=dict)):
        plan = (payload or {}).get("plan")
        if not plan:
            return JSONResponse({"error": "plan is required"}, status_code=400)
        try:
            t = await _tenants.update_plan(tid, plan)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if t is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_tenant_view(t))


# ---------------------------------------------------------------------------
# Audit + DLQ replay (under /api/admin so the existing admin gate applies)
# ---------------------------------------------------------------------------
def _register_admin_routes(app: FastAPI) -> None:
    @app.get("/api/admin/audit")
    async def audit_query(
        tenant: str | None = Query(default=None),
        route: str | None = Query(default=None),
        method: str | None = Query(default=None),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        rows = _s.query_audit(
            tenant_id=tenant,
            route_prefix=route,
            method=method,
            since=since,
            until=until,
            limit=limit,
        )
        return JSONResponse({"entries": rows, "total": len(rows), "filters": {
            "tenant": tenant, "route": route, "method": method,
            "since": since, "until": until, "limit": limit,
        }})

    @app.post("/api/admin/dlq/{name}/replay")
    async def dlq_replay_post(name: str, dry_run: bool = False):
        # Reuse the registry from server.py via runtime import to avoid a cycle.
        try:
            from war_room.dashboard.server import _DLQ_REGISTRY
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"DLQ registry unavailable: {exc}"}, status_code=500)
        if name not in _DLQ_REGISTRY:
            return JSONResponse({"error": f"unknown dlq {name!r}", "known": list(_DLQ_REGISTRY)}, status_code=404)
        path = Path(_DLQ_REGISTRY[name])
        result = await _replay.replay_dlq(path, dry_run=dry_run)
        return JSONResponse({"name": name, "dry_run": dry_run, **result, "kinds_with_handlers": _replay.known_kinds()})

    @app.get("/api/admin/dlq/{name}/replay-status")
    async def dlq_replay_status(name: str):
        return JSONResponse(
            {
                "name": name,
                "handlers": _replay.known_kinds(),
                "max_attempts": _replay.MAX_ATTEMPTS,
                "recent": _replay.replay_log_tail(50),
            }
        )

    @app.get("/api/admin/v1/overview")
    async def v1_overview():
        # One-stop dashboard payload that the SPA fetches on load.
        tenants = await _tenants.list_tenants(include_deleted=True)
        try:
            from war_room.dashboard.server import _DLQ_REGISTRY
            dlq_names = list(_DLQ_REGISTRY.keys())
        except Exception:
            dlq_names = []
        recent_audit = _s.query_audit(limit=25)
        items = await _spotlight.list_items()
        usage_rows = await _usage.rollup()
        return JSONResponse(
            {
                "v1_version": V1_VERSION,
                "tenants": [_tenant_view(t) for t in tenants],
                "tenant_count": sum(1 for t in tenants if t.status != "deleted"),
                "dlq_names": dlq_names,
                "audit_recent": recent_audit,
                "audit_count": len(recent_audit),
                "replay_handlers": _replay.known_kinds(),
                "replay_recent": _replay.replay_log_tail(20),
                "adapters": _adapters.all_probes(),
                "spotlight": [item.to_dict() for item in items],
                "spotlight_impressions": _spotlight.impression_counts(),
                "usage": usage_rows,
                "sse_subscribers": _sse.subscriber_count(),
            }
        )

    @app.post("/api/admin/audit/gc")
    async def audit_gc(keep_days: int = Query(default=90, ge=1, le=3650)):
        removed = _audit.gc_audit(keep_days=keep_days)
        return JSONResponse({"removed_shards": removed, "keep_days": keep_days})


# ---------------------------------------------------------------------------
# Adapters (Discord, Linear, Notion, Jira)
# ---------------------------------------------------------------------------
def _register_adapters(app: FastAPI) -> None:
    @app.get("/api/v1/adapters")
    async def list_adapters():
        return JSONResponse({"adapters": _adapters.all_probes()})

    @app.post("/api/v1/adapters/{adapter_id}/sync")
    async def adapter_sync(adapter_id: str, limit: int = Query(default=25, ge=1, le=100)):
        adapter = _adapters.get(adapter_id)
        if adapter is None:
            return JSONResponse({"error": f"unknown adapter {adapter_id!r}", "known": list(_adapters.REGISTRY)}, status_code=404)
        probe = adapter.probe()
        if not probe.get("ok"):
            return JSONResponse({"ok": False, "probe": probe, "ingested": 0})
        tasks: list[dict] = []
        try:
            async for task in adapter.ingest(limit=limit):
                tasks.append(task)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "probe": probe, "error": str(exc)}, status_code=502)
        _sse.publish("adapter_sync", {"adapter": adapter_id, "ingested": len(tasks)})
        return JSONResponse({"ok": True, "probe": probe, "ingested": len(tasks), "tasks": tasks})


# ---------------------------------------------------------------------------
# Dialog preview — shows citations + convergence without needing a full task DB
# ---------------------------------------------------------------------------
def _register_dialog_preview(app: FastAPI) -> None:
    @app.post("/api/v1/dialog/preview")
    async def dialog_preview(payload: dict = Body(default_factory=dict)):
        """Compose a preview dialog for an ad-hoc task and demonstrate v1
        annotations: inline citations + convergence score for an optional
        comment thread.
        """
        task = {
            "title": (payload or {}).get("title") or "Demo task",
            "description": (payload or {}).get("description") or "",
            "status": "open",
            "assigned_agents": (payload or {}).get("assigned_agents") or ["research", "writer"],
        }
        # Compose without hitting Supabase/LLM by using the dialog module directly.
        from war_room.tasks import dialog as _dialog
        try:
            lines = await _dialog.compose_dialog(task)
        except Exception as exc:  # noqa: BLE001
            logger.warning("preview compose failed: %s", exc)
            lines = [
                {"agent_id": "semeclaw", "role": "Orchestrator", "text": f"Preview unavailable: {exc}", "ts": _s.utcnow_iso()}
            ]
        enriched = _cites.attach_citations(lines, task)
        evidence = _cites.collect_evidence_ids(enriched)

        # Convergence over an optional `comments` array (turn-by-turn).
        comments: list[str] = list((payload or {}).get("comments") or [])
        signals = []
        for i, comment in enumerate(comments, start=1):
            signal = _conv.score_convergence(
                turn=i,
                new_comment=comment,
                prior_comments=comments[: i - 1],
                prior_replies=[l.get("text", "") for l in enriched],
            )
            signals.append({"turn": i, "comment": comment, **signal.__dict__})

        return JSONResponse(
            {
                "task": task,
                "lines": enriched,
                "evidence": evidence,
                "convergence": signals,
                "v1_version": V1_VERSION,
            }
        )


# ---------------------------------------------------------------------------
# Spotlight + ad rotation
# ---------------------------------------------------------------------------
SPOTLIGHT_PAGE_TEMPLATE = """<!doctype html>
<html lang=en>
<head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>SemeClaw · Subscriber Spotlight</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; padding:48px 24px; font:16px/1.55 ui-sans-serif, system-ui;
         background:#0b0d12; color:#e5e7eb; }
  .wrap { max-width:1100px; margin:0 auto; }
  h1 { letter-spacing:-0.02em; font-weight:700; margin:0 0 6px; font-size:30px; }
  .lead { color:#9ca3af; margin:0 0 36px; }
  .grid { display:grid; gap:22px; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }
  article { background:linear-gradient(180deg,#15171c,#0f1115); border:1px solid #21242d;
            border-radius:18px; padding:22px; box-shadow:0 24px 60px -28px rgba(0,0,0,.7); }
  .logo { width:54px; height:54px; border-radius:14px; display:grid; place-items:center;
          font-family:"Playfair Display", ui-serif, Georgia; font-size:30px; color:#fff;
          margin-bottom:14px; }
  h3 { margin:0 0 4px; font-size:18px; }
  .tag { color:#a1a1aa; font-size:13px; margin:0 0 10px; }
  .desc { color:#cbd5e1; font-size:14px; margin:0 0 14px; }
  ul { margin:0 0 14px 18px; padding:0; color:#a1a1aa; font-size:13px; }
  a.cta { color:#fde68a; font-weight:600; border-bottom:1px solid #5b4a13; text-decoration:none; }
  .pin { font-size:11px; padding:2px 8px; border-radius:999px; background:rgba(245,158,11,0.15);
          color:#fde68a; border:1px solid rgba(245,158,11,0.4); margin-left:8px; }
</style></head>
<body>
<div class=wrap>
  <h1>Subscriber Spotlight</h1>
  <p class=lead>Featured projects in the SemeClaw War Room. Anchors stay; rotating slots refresh every $TTL_DAYS$ days.</p>
  <div class=grid>$ITEMS$</div>
</div>
<script>
  fetch('/api/v1/spotlight').then(r=>r.json()).then(j=>{
    j.items && j.items.forEach(item => {
      fetch('/api/v1/spotlight/impression', { method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ item_id: item.id, source: 'spotlight_page' }) });
    });
  }).catch(()=>{});
</script>
</body></html>"""


def _spotlight_card_html(item) -> str:
    bullets = "".join(f"<li>{b}</li>" for b in (item.bullets or []))
    pin = "<span class=pin>Anchor</span>" if item.pinned else ""
    return f"""
      <article style="--rgb:{item.accent_rgb}">
        <div class=logo style="background:linear-gradient(135deg,{item.gradient_from},{item.gradient_to});">{item.logo_letter}</div>
        <h3>{item.name}{pin}</h3>
        <p class=tag>{item.tagline}</p>
        <p class=desc>{item.description}</p>
        {f'<ul>{bullets}</ul>' if bullets else ''}
        <a class=cta href="{item.url}" target=_blank rel=noopener>Visit ↗</a>
      </article>
    """


def _register_spotlight(app: FastAPI) -> None:
    @app.get("/api/v1/spotlight")
    async def spotlight_list(include_ineligible: bool = False):
        items = await _spotlight.list_items(include_ineligible=include_ineligible)
        return JSONResponse({"items": [item.to_dict() for item in items], "count": len(items)})

    @app.post("/api/v1/spotlight/overrides")
    async def spotlight_add(payload: dict = Body(default_factory=dict)):
        if not payload.get("name"):
            return JSONResponse({"error": "name is required"}, status_code=400)
        item = await _spotlight.add_override(payload)
        _sse.publish("spotlight_changed", {"action": "add", "id": item.id})
        return JSONResponse(item.to_dict(), status_code=201)

    @app.delete("/api/v1/spotlight/overrides/{item_id}")
    async def spotlight_delete(item_id: str):
        ok = await _spotlight.remove_override(item_id)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        _sse.publish("spotlight_changed", {"action": "remove", "id": item_id})
        return JSONResponse({"removed": item_id})

    @app.post("/api/v1/spotlight/impression")
    async def spotlight_impression(payload: dict = Body(default_factory=dict)):
        item_id = (payload or {}).get("item_id")
        if not item_id:
            return JSONResponse({"error": "item_id is required"}, status_code=400)
        await _spotlight.record_impression(
            item_id,
            source=(payload or {}).get("source") or "api",
            tenant_id=(payload or {}).get("tenant_id"),
        )
        return JSONResponse({"ok": True})

    @app.get("/api/v1/ads/next")
    async def ads_next(source: str = Query(default="api"), tenant_id: str | None = Query(default=None)):
        item = await _spotlight.pick_one()
        if item is None:
            return JSONResponse({"item": None})
        await _spotlight.record_impression(item.id, source=source, tenant_id=tenant_id)
        return JSONResponse({"item": item.to_dict()})

    @app.get("/spotlight", response_class=HTMLResponse)
    async def spotlight_page():
        items = await _spotlight.list_items()
        cards = "".join(_spotlight_card_html(item) for item in items) or "<p>No spotlight items yet.</p>"
        # Approximate ttl_days from source for the lead copy.
        ttl_days = (_spotlight._source_payload().get("ttl_days") or 7)
        html = SPOTLIGHT_PAGE_TEMPLATE.replace("$TTL_DAYS$", str(ttl_days)).replace("$ITEMS$", cards)
        return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Per-tenant usage + quota
# ---------------------------------------------------------------------------
def _register_usage(app: FastAPI) -> None:
    @app.get("/api/tenants/{tid}/usage")
    async def tenant_usage(tid: str, period: str | None = Query(default=None)):
        tenant = await _tenants.get_tenant(tid)
        if tenant is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        snap = await _usage.snapshot(tid, period=period)
        check = await _usage.check_quota(tid, kind="meetings")
        return JSONResponse(
            {
                "tenant_id": tid,
                "plan": tenant.plan,
                "limits": _tenants.plan_limits(tenant.plan),
                "usage": snap,
                "quota": {
                    "allowed": check.allowed,
                    "reason": check.reason,
                    "used": check.used,
                    "limit": check.limit,
                },
            }
        )

    @app.post("/api/tenants/{tid}/usage/increment")
    async def tenant_usage_increment(tid: str, payload: dict = Body(default_factory=dict)):
        deltas = {k: int(v) for k, v in (payload or {}).items() if isinstance(v, (int, float))}
        bucket = await _usage.increment(tid, **deltas)
        _sse.publish("usage_incremented", {"tenant_id": tid, **bucket})
        return JSONResponse({"ok": True, "tenant_id": tid, "bucket": bucket})

    @app.get("/api/admin/v1/usage")
    async def admin_usage_rollup(period: str | None = Query(default=None)):
        return JSONResponse({"period": period or _usage._period_key(), "rows": await _usage.rollup(period=period)})


# ---------------------------------------------------------------------------
# Server-sent events
# ---------------------------------------------------------------------------
def _register_sse(app: FastAPI) -> None:
    @app.get("/api/admin/v1/events")
    async def admin_events():
        # Streamed under /api/admin so the existing admin gate enforces auth.
        return StreamingResponse(_sse.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform"})


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------
def _register_exports(app: FastAPI) -> None:
    @app.get("/api/admin/audit.csv")
    async def audit_csv(
        tenant: str | None = Query(default=None),
        route: str | None = Query(default=None),
        method: str | None = Query(default=None),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        limit: int = Query(default=1000, ge=1, le=10000),
    ):
        rows = _s.query_audit(tenant_id=tenant, route_prefix=route, method=method, since=since, until=until, limit=limit)
        body = _exports.audit_csv(rows)
        return StreamingResponse(
            iter([body]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=semeclaw_audit.csv"},
        )

    @app.get("/api/admin/v1/usage.csv")
    async def usage_csv(period: str | None = Query(default=None)):
        rows = await _usage.rollup(period=period)
        body = _exports.usage_csv(rows)
        return StreamingResponse(
            iter([body]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=semeclaw_usage.csv"},
        )


# ---------------------------------------------------------------------------
# /admin SPA
# ---------------------------------------------------------------------------
def _register_admin_spa(app: FastAPI) -> None:
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_root():
        index = ADMIN_STATIC_DIR / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>SemeClaw admin SPA missing</h1>", status_code=500)
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get("/admin/static/{filename:path}")
    async def admin_static(filename: str):
        # Defend against path traversal — only allow files inside ADMIN_STATIC_DIR.
        target = (ADMIN_STATIC_DIR / filename).resolve()
        if not str(target).startswith(str(ADMIN_STATIC_DIR.resolve())) or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(str(target))
