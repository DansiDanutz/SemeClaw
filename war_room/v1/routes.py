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
from war_room.v1 import billing as _billing
from war_room.v1 import citations as _cites
from war_room.v1 import convergence as _conv
from war_room.v1 import dlq_replay as _replay
from war_room.v1 import exports as _exports
from war_room.v1 import quota as _quota
from war_room.v1 import spotlight as _spotlight
from war_room.v1 import sse as _sse
from war_room.v1 import storage as _s
from war_room.v1 import tenants as _tenants
from war_room.v1 import usage as _usage
from war_room.v1 import webhooks as _webhooks
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
    _quota.install(app)
    _audit.install(app)
    _register_health(app)
    _register_about(app)
    _register_tenants(app)
    _register_admin_routes(app)
    _register_adapters(app)
    _register_dialog_preview(app)
    _register_spotlight(app)
    _register_usage(app)
    _register_sse(app)
    _register_exports(app)
    _register_billing(app)
    _register_webhooks(app)
    _register_local_tasks(app)
    _register_embed_v1(app)
    _register_admin_spa(app)


# ---------------------------------------------------------------------------
# /api/v1/health — subsystem aggregation
# ---------------------------------------------------------------------------
def _register_health(app: FastAPI) -> None:
    @app.get("/api/v1/health")
    async def v1_health():
        """Aggregate health check covering: data dir writability, adapter
        probes, billing config, and DLQ depth. Returns 200 even when
        sub-systems are degraded — the caller inspects the body for
        per-subsystem state. Returns 503 only when the data dir itself
        cannot accept writes (i.e. the v1 surface cannot persist).
        """
        from war_room.v1 import adapters as _adapters
        from war_room.v1 import tenants as _tenants_mod

        data_dir = _s.V1_DATA_DIR.resolve()
        data_dir_writable = True
        data_dir_error: str | None = None
        probe_path = Path(data_dir) / ".health_probe"
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
        except OSError as exc:
            data_dir_writable = False
            data_dir_error = str(exc)

        # Tenant count is the cheapest signal that storage is usable.
        try:
            tenants_seen = len(await _tenants_mod.list_tenants(include_deleted=True))
        except Exception as exc:  # noqa: BLE001
            tenants_seen = -1
            logger.warning("health: tenant count failed: %s", exc)

        adapter_state = []
        for probe in _adapters.all_probes():
            adapter_state.append(
                {"id": probe["id"], "ok": probe["ok"], "missing": probe.get("missing", [])}
            )

        # DLQ depth — count lines in each known JSONL without loading it.
        dlq_depth: dict[str, int] = {}
        try:
            from war_room.dashboard.server import _DLQ_REGISTRY

            for name, raw_path in _DLQ_REGISTRY.items():
                p = Path(raw_path)
                if not p.exists():
                    dlq_depth[name] = 0
                    continue
                # Quick line count without loading the whole file into memory.
                count = 0
                with p.open("rb") as f:
                    for _ in f:
                        count += 1
                dlq_depth[name] = count
        except Exception as exc:  # noqa: BLE001
            logger.info("health: DLQ depth scan skipped: %s", exc)

        body = {
            "ok": data_dir_writable,
            "version": V1_VERSION,
            "data_dir": str(data_dir),
            "data_dir_writable": data_dir_writable,
            "data_dir_error": data_dir_error,
            "tenants_total": tenants_seen,
            "adapters": adapter_state,
            "billing_configured": _billing.is_configured(),
            "billing_pending": _billing.pending_count(),
            "dlq_depth": dlq_depth,
        }
        status = 200 if data_dir_writable else 503
        return JSONResponse(body, status_code=status)


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
                    "stripe_billing",
                    "stripe_webhooks",
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
                    "billing": [
                        "GET /api/v1/billing/status",
                        "POST /api/v1/billing/customer",
                        "POST /api/v1/billing/flush",
                        "POST /api/v1/billing/webhook",
                    ],
                    "admin": ["GET /admin"],
                },
                "billing_configured": _billing.is_configured(),
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
# Stripe billing
# ---------------------------------------------------------------------------
def _register_billing(app: FastAPI) -> None:
    @app.post("/api/v1/billing/customer")
    async def billing_create_customer(payload: dict = Body(default_factory=dict)):
        tid = (payload or {}).get("tenant_id")
        if not tid:
            return JSONResponse({"error": "tenant_id is required"}, status_code=400)
        tenant = await _tenants.get_tenant(tid)
        if tenant is None:
            return JSONResponse({"error": "tenant not found"}, status_code=404)
        cid = await _billing.ensure_customer(tenant)
        return JSONResponse({"tenant_id": tid, "stripe_customer_id": cid, "configured": _billing.is_configured()})

    @app.post("/api/v1/billing/flush")
    async def billing_flush():
        result = await _billing.flush()
        return JSONResponse({"pending_before": result.get("queued", 0), **result})

    @app.get("/api/v1/billing/status")
    async def billing_status():
        return JSONResponse(
            {
                "configured": _billing.is_configured(),
                "pending": _billing.pending_count(),
                "required_env": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"],
                "optional_env": [
                    "STRIPE_PRICE_METER_MEETINGS",
                    "STRIPE_PRICE_METER_TTS",
                    "STRIPE_PRICE_METER_LLM",
                    "STRIPE_SUB_ITEM_{KIND}_{PLAN}",
                ],
            }
        )

    @app.post("/api/v1/billing/webhook")
    async def billing_webhook(request: Request):
        # Stripe sends "Stripe-Signature" header + JSON body. We verify HMAC.
        payload = await request.body()
        sig = request.headers.get("stripe-signature") or request.headers.get("Stripe-Signature") or ""
        event = _billing.verify_webhook(payload, sig)
        if event is None:
            return JSONResponse({"error": "invalid_signature_or_secret"}, status_code=400)
        outcome = await _billing.apply_webhook(event)
        _sse.publish("billing_webhook", {"type": event.type, **outcome})
        return JSONResponse({"received": True, "type": event.type, **outcome})


# ---------------------------------------------------------------------------
# Webhook subscriptions
# ---------------------------------------------------------------------------
def _register_webhooks(app: FastAPI) -> None:
    @app.post("/api/v1/webhooks")
    async def webhooks_subscribe(payload: dict = Body(default_factory=dict)):
        tenant_id = (payload or {}).get("tenant_id")
        url = (payload or {}).get("url")
        events = (payload or {}).get("events") or []
        if not tenant_id or not url:
            return JSONResponse({"error": "tenant_id and url are required"}, status_code=400)
        try:
            sub = await _webhooks.subscribe(tenant_id=tenant_id, url=url, events=list(events))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "id": sub.id,
                "tenant_id": sub.tenant_id,
                "url": sub.url,
                "events": list(sub.events),
                "status": sub.status,
                "secret": sub.secret,
                "secret_warning": "Store this secret now — it is used to verify HMAC signatures.",
            },
            status_code=201,
        )

    @app.get("/api/v1/webhooks")
    async def webhooks_list(tenant: str | None = Query(default=None)):
        subs = await _webhooks.list_subscriptions(tenant_id=tenant)
        return JSONResponse(
            {
                "subscriptions": [
                    {
                        "id": s.id,
                        "tenant_id": s.tenant_id,
                        "url": s.url,
                        "events": list(s.events),
                        "status": s.status,
                        "created_at": s.created_at,
                        "last_delivery_at": s.last_delivery_at,
                        "last_status": s.last_status,
                    }
                    for s in subs
                ]
            }
        )

    @app.delete("/api/v1/webhooks/{sub_id}")
    async def webhooks_unsubscribe(sub_id: str):
        ok = await _webhooks.unsubscribe(sub_id)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"deleted": sub_id})

    @app.post("/api/v1/webhooks/test")
    async def webhooks_test(payload: dict = Body(default_factory=dict)):
        event = (payload or {}).get("event") or "test.ping"
        tenant_id = (payload or {}).get("tenant_id")
        data = (payload or {}).get("data") or {"hello": "world"}
        scheduled = await _webhooks.dispatch(event, data, tenant_id=tenant_id)
        return JSONResponse({"event": event, "scheduled": scheduled})


# ---------------------------------------------------------------------------
# Local tasks (when Supabase isn't wired)
# ---------------------------------------------------------------------------
def _register_local_tasks(app: FastAPI) -> None:
    try:
        from war_room.v1 import local_tasks as _lt
    except Exception:  # pragma: no cover
        return

    if not _lt.should_activate():
        return

    @app.on_event("startup")
    async def _seed_demo_task():  # noqa: D401 — FastAPI hook
        try:
            await _lt.seed_demo_task()
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_demo_task failed: %s", exc)

    @app.get("/api/v1/tasks")
    async def v1_tasks_list(tenant: str = Query(default="default"), status: str | None = Query(default=None)):
        rows = await _lt.list_tasks(tenant_id=tenant, status=status)
        return JSONResponse({"tasks": rows, "total": len(rows), "store": "local"})

    @app.post("/api/v1/tasks")
    async def v1_tasks_create(payload: dict = Body(default_factory=dict)):
        title = (payload or {}).get("title")
        if not title:
            return JSONResponse({"error": "title is required"}, status_code=400)
        tid = await _lt.upsert_task(
            source=(payload or {}).get("source") or "local",
            source_id=(payload or {}).get("source_id") or f"ad-hoc-{_s.utcnow_iso()}",
            tenant_id=(payload or {}).get("tenant_id") or "default",
            title=title,
            description=(payload or {}).get("description") or "",
            status=(payload or {}).get("status") or "open",
            assigned_agents=(payload or {}).get("assigned_agents") or ["research", "writer"],
            meta=(payload or {}).get("meta") or {},
        )
        task = await _lt.get_task(tid)
        _sse.publish("task_created", {"task_id": tid})
        return JSONResponse(task, status_code=201)


# ---------------------------------------------------------------------------
# Embed v1 JS SDK — ad serving + spotlight rendering
# ---------------------------------------------------------------------------
EMBED_V1_JS = r"""(function () {
  var BASE = window.__SEMECLAW_BASE__ || (location.origin || "");
  var styleInjected = false;
  function inject(css) {
    if (styleInjected) return;
    styleInjected = true;
    var s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }
  function fmt(card) {
    var bullets = (card.bullets || []).map(function (b) { return "<li>" + escape(b) + "</li>"; }).join("");
    return (
      '<article class="sc-ad" data-id="' + card.id + '">' +
      '  <div class="sc-ad__logo" style="background:linear-gradient(135deg,' + card.gradient_from + ',' + card.gradient_to + ');">' + escape(card.logo_letter || "?") + '</div>' +
      '  <h3>' + escape(card.name) + (card.pinned ? ' <span class="sc-ad__pin">Anchor</span>' : "") + "</h3>" +
      "  <p>" + escape(card.tagline || "") + "</p>" +
      (bullets ? '<ul class="sc-ad__bullets">' + bullets + "</ul>" : "") +
      '  <a class="sc-ad__cta" href="' + escape(card.url || "#") + '" target="_blank" rel="noopener">Visit ↗</a>' +
      "</article>"
    );
  }
  function escape(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function injectCss() {
    inject(
      ".sc-ad{font:14px/1.5 system-ui,sans-serif;background:linear-gradient(180deg,#15171c,#0f1115);" +
      "border:1px solid #21242d;border-radius:14px;padding:18px;color:#e5e7eb;max-width:340px;" +
      "box-shadow:0 24px 60px -28px rgba(0,0,0,.6);}" +
      ".sc-ad__logo{width:46px;height:46px;border-radius:12px;display:grid;place-items:center;" +
      "font-family:'Playfair Display',ui-serif,Georgia,serif;font-size:26px;color:#fff;margin-bottom:10px;}" +
      ".sc-ad h3{margin:0 0 4px;font-size:16px;}" +
      ".sc-ad p{margin:0 0 10px;color:#a1a1aa;font-size:13px;}" +
      ".sc-ad__bullets{margin:0 0 12px 18px;padding:0;font-size:12.5px;color:#a1a1aa;}" +
      ".sc-ad__cta{color:#fde68a;font-weight:600;border-bottom:1px solid #5b4a13;text-decoration:none;}" +
      ".sc-ad__pin{font-size:10.5px;padding:2px 7px;border-radius:999px;background:rgba(245,158,11,.15);" +
      "color:#fde68a;border:1px solid rgba(245,158,11,.4);margin-left:6px;}"
    );
  }
  async function adNext(opts) {
    opts = opts || {};
    var source = opts.source || "embed-v1";
    var params = "source=" + encodeURIComponent(source);
    if (opts.tenant_id) params += "&tenant_id=" + encodeURIComponent(opts.tenant_id);
    var r = await fetch(BASE + "/api/v1/ads/next?" + params);
    var json = await r.json();
    return json.item || null;
  }
  async function renderAd(target, opts) {
    var el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) throw new Error("renderAd: target not found");
    injectCss();
    var card = await adNext(opts || {});
    if (!card) {
      el.innerHTML = '<p style="color:#94a3b8;font:14px system-ui">No spotlight items eligible.</p>';
      return null;
    }
    el.innerHTML = fmt(card);
    return card;
  }
  async function spotlight() {
    var r = await fetch(BASE + "/api/v1/spotlight");
    return (await r.json()).items || [];
  }
  async function renderSpotlight(target) {
    var el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) throw new Error("renderSpotlight: target not found");
    injectCss();
    var items = await spotlight();
    el.innerHTML = items.map(fmt).join("") || '<p style="color:#94a3b8;font:14px system-ui">No spotlight items.</p>';
    return items;
  }
  function autoMount() {
    document.querySelectorAll("[data-semeclaw-ad]").forEach(function (n) {
      renderAd(n, { source: n.getAttribute("data-source") || "auto", tenant_id: n.getAttribute("data-tenant") || null }).catch(function () {});
    });
    document.querySelectorAll("[data-semeclaw-spotlight]").forEach(function (n) {
      renderSpotlight(n).catch(function () {});
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMount);
  } else {
    autoMount();
  }
  // Extend the existing window.SemeClaw object if present, otherwise create.
  window.SemeClaw = Object.assign(window.SemeClaw || {}, {
    base: BASE, adNext: adNext, renderAd: renderAd, spotlight: spotlight, renderSpotlight: renderSpotlight,
  });
})();
"""


def _register_embed_v1(app: FastAPI) -> None:
    @app.get("/embed-v1.js")
    async def embed_v1_js():
        from fastapi.responses import Response

        return Response(
            EMBED_V1_JS,
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
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
