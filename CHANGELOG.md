# Changelog

All notable changes to SemeClaw will be documented in this file.

## [0.8.8] - 2026-04-24

### Fixed — CI lint job + missing runtime dep + flaky tests

- **`[dependency-groups]` dev in `pyproject.toml`** with `ruff>=0.6` and
  `mypy>=1.11`. The v0.7.15 / v0.8.0 PR added `[tool.ruff]` config and a
  strict `uv run ruff check` step to `ci.yml` but never declared ruff as
  a dependency — the CI job failed in 13s with `error: Failed to spawn:
  ruff`. CI's `Install dependencies` step now uses
  `uv sync --frozen --group dev || uv sync --group dev`.
- **`prometheus-client>=0.20.0`** added to `[project] dependencies`.
  The v0.8.1 `/metrics` extension imports `prometheus_client` and
  degrades to a stub when the library is missing; the dep was never
  declared, so production `/metrics` responses returned
  `# prometheus_client not installed - metrics disabled.` instead of the
  new Counter/Gauge/Histogram registry the PR promised.
- **`war_room/tests/test_adapters.py::test_health_no_token`** — assertion
  updated to tolerate the new combined error string
  `"TELEGRAM_BOT_TOKEN / SEMECLAW_BOT_TOKEN not set"` the adapter emits.
- **`war_room/tests/test_health_deep.py`** — fixture now force-clears
  `SUPA_URL` / `SUPA_KEY` / `STRIPE_SECRET_KEY` on the deps module after
  reload. Previously the test relied on the env being bare, which meant
  any dev with a local `.env` saw a false failure (`load_dotenv()`
  repopulated the vars before `_check_supabase` read them).
- **`war_room/tests/test_version_policy.py`** — removed unused `pytest`
  import (ruff F401).
- Regenerated `uv.lock` against the new dep set.

## [0.8.7] - 2026-04-24

### Added — Order-independent shared-httpx test

- `war_room/tests/test_shared_http_client.py::test_tasks_db_uses_shared_client`
  was flaky in full-suite order because it reloaded the `_db` module.
  Rewritten as self-contained with direct `monkeypatch.setattr` on
  module symbols. Now order-independent.

## [0.8.6] - 2026-04-24

### Added — DLQ admin endpoints (/api/admin/dlq)

- `GET /api/admin/dlq` — list every registered DLQ with path, existence,
  size in bytes, and line count.
- `GET /api/admin/dlq/{name}?head=N&tail=M` — return up to `head` top
  entries and/or `tail` bottom entries parsed from the JSONL. Safe cap
  of 500 lines per side.
- `POST /api/admin/dlq/{name}/drain` — renames the DLQ file aside with a
  `.drained-<ts>` suffix. Does NOT attempt automatic replay — entries
  require human judgement or a DLQ-kind-specific handler.
- New `_semeclaw_admin_gate` middleware: `/api/admin/*` is bearer-gated
  on *every* method (including GET), separate from the existing
  write-only gate. If `SEMECLAW_API_KEY` is unset the endpoint returns
  503 — an operator must explicitly configure it to access DLQs.
- Registry currently covers `adclaw` and `tasks` DLQs; extend by adding
  to `_DLQ_REGISTRY` in `server.py`.
- 6 unit tests: list/peek/drain happy paths, 401 without bearer, 404 on
  unknown name, 503 when `SEMECLAW_API_KEY` isn't set.

## [0.8.5] - 2026-04-24

### Added — Meeting replay API (/api/meeting/{id}/replay)

- `GET /api/meeting/{meeting_id}/replay?speed=N&cap=M` in server.py,
  adjacent to the transcript backfill endpoint shipped in v0.7.15.
- Reads the persisted per-meeting JSONL, reconstructs inter-event delays
  from `ts` / `viewed_at` / `at` fields (either numeric seconds or
  ISO-8601), and returns `frames=[{seq, delay_ms, event}, …]`. A client
  simply `await sleep(delay_ms/1000)` between frames to reproduce the
  pacing — speed multiplier divides the delay so a late viewer can
  "catch up" fast and then watch live.
- Without timestamps, frames default to an 80 ms steady gap — still
  watchable.
- Safety caps: `speed` clamped to [0.01, 50], `cap` clamped to [1, 10000].
- 4 unit tests cover frame timing, speed multiplier, unknown-meeting
  empty response, and the cap.

## [0.8.4] - 2026-04-24

### Added — Structured JSON logs + request-id correlation

- `war_room/utils/logging_config.py` — `setup_logging()` switches to JSON
  lines when `SEMECLAW_LOG_JSON=1` (otherwise keeps the existing human
  format). Fields: `ts`, `level`, `logger`, `message`, `request_id`, and
  `exc` if an exception was logged.
- Request-id contextvar + `_RequestIdFilter` so every `logger.*` call
  picks up the current request's id without threading it by hand.
- `_semeclaw_request_id` middleware honours an incoming `X-Request-ID`
  header or generates a 12-char hex id. Returned in the response header
  for client-side correlation.
- `SEMECLAW_LOG_LEVEL` (default `INFO`) also configurable via env.
- 5 unit tests cover id generation, contextvar round-trip, JSON payload
  shape, inbound-header honour, and auto-generation.

## [0.8.3] - 2026-04-24

### Added — Shared httpx.AsyncClient pool

- `war_room/utils/http_client.py` — process-wide shared `httpx.AsyncClient`
  pool keyed by `base_url`. Reuses connections so steady-state Supabase
  latency drops by 20-80 ms per call vs creating a new client per request.
- Connection pool sized generously (`max_connections=100`,
  `max_keepalive_connections=20`) so traffic bursts don't queue.
- `tasks/_db._supa_once` now delegates to `get_shared_client` instead of
  `async with httpx.AsyncClient(...)`.
- Server lifespan calls `close_shared_clients()` on shutdown so Fly's
  rolling deploys don't leak sockets.
- 4 new tests verify identity (same base_url → same client), isolation
  (different base_urls → different clients), recreate-after-close, and
  tasks/_db wiring.

## [0.8.2] - 2026-04-24

### Added — Deep health endpoint (/api/health/deep)

- `GET /api/health/deep` in `war_room/dashboard/routes/health.py`.
- Six subsystem checks: **supabase** (REST ping with 3s budget), **stripe**
  (`/v1/charges?limit=1` probe only if `STRIPE_SECRET_KEY` set), **tts**
  (env presence for ElevenLabs / edge-tts / Kokoro), **dlq** (reports
  adclaw and tasks DLQ sizes, degrades above `SEMECLAW_DLQ_WARN_BYTES`
  default 10 MiB), **disk** (`shutil.disk_usage` on
  `SEMECLAW_DATA_DIR`, degrades above 90% used), **version**.
- Response rolls up to `ok` / `degraded` / `down`. HTTP 503 only when at
  least one subsystem is truly `down`; `degraded` returns 200 so paging
  is operator choice.
- Shallow `/api/agent/health` kept unchanged so Fly's cheap healthcheck
  is unaffected.
- 3 unit tests cover shape, unconfigured-Supabase degradation, and DLQ
  byte reporting.

## [0.8.1] - 2026-04-24

### Added — Prometheus metrics — /metrics extension + counter wiring

- `war_room/dashboard/metrics.py` — new module that declares the full
  `prometheus_client` registry (Counters, Gauges, Histograms) with graceful
  fallback when `prometheus-client` isn't installed.
- `war_room/dashboard/routes/billing.py` `/metrics` endpoint now emits both
  Dan's hand-rolled `_METRICS` dict AND the `prometheus_client` registry,
  so a single Prometheus scrape covers everything.
- `war_room/dashboard/server.py` gains a metrics middleware that counts
  every HTTP response by method+status-class and records a per-path latency
  histogram (path truncated to 64 chars to avoid cardinality blowups).
  Instrumentation is best-effort — failures never break the response.
- `websocket_manager.py` bumps `semeclaw_ws_connections_active` on
  connect/disconnect and `semeclaw_meeting_broadcasts_total{type=...}` on
  every broadcast.
- `adclaw/server.py` increments `semeclaw_impressions_total{tenant_id}`
  and `semeclaw_dlq_appends_total{dlq}` on the hot impression path.
- `pyproject.toml`: `prometheus-client>=0.20.0` added.
- 4 new tests cover endpoint content, HTTP counter increment, public
  access, and the no-prom-client fallback path.

## [0.8.0] - 2026-04-24

### Added — versioning policy + CI enforcement

- `VERSIONING.md` documents the semver rules (patch/minor/major), how
  `scripts/bump_version.py` is used, and what CI enforces.
- `scripts/check_version_bumped.sh` fails a PR if `pyproject.toml`
  version equals `main`'s and any non-docs file was touched. Pure
  docs-only PRs are allowed through.
- CI: new "Enforce version bump" step runs on `pull_request` events.
  `fetch-depth: 0` added to the checkout so the script can diff against
  `origin/main`.
- Tests: `war_room/tests/test_version_policy.py` smoke-covers script
  existence, executability, VERSIONING.md headings, and CHANGELOG.md
  structure.

### Policy

From now on every PR bumps the version. `X-SemeClaw-Version` and
`/api/agent/manifest` stay meaningful.

## [0.7.15] - 2026-04-24

### Changed — lint/format tree-wide + CI ruff enforcement

- `pyproject.toml`: added `[tool.ruff]`, `[tool.ruff.lint]`, `[tool.mypy]`,
  and `[tool.pytest.ini_options]` config blocks.
- Applied `ruff check --fix` (247 auto-fixes: unused imports, import sort,
  `UP` modernisations, quoted annotations, f-string placeholders) and
  `ruff format` across `src/`, `war_room/`, `adclaw/`, `nervix_platform/`
  (102 files reformatted, 58 already clean).
- 11 pre-existing rule categories ignored in config with a comment pointing
  at the ratchet path. No strict-new-code rules were weakened.
- CI: `ruff check` and `ruff format --check` now fail the build if new
  code regresses. Previously both were run with `|| true`.

### Tests

- 395 passing, 4 pre-existing sandbox failures, 1 skipped (no regressions).

## [0.7.14] - 2026-04-24

### Added — Tasks system (Phases A → D)

- **Phase A — Tasks ingest + dialog v1**
  - `semeclaw_tasks`, `semeclaw_dialogs`, `semeclaw_interventions` Supabase tables
    plus `semeclaw_upsert_task`, `semeclaw_quota`, `semeclaw_enforce_retention` RPCs.
  - Adapters auto-discover tasks from Paperclip, Moltica, local JSON files,
    Claude Code workspace.
  - `compose_dialog()` produces 6-line meetings with deterministic templates
    (zero-key) or OpenRouter free models (Qwen3-Next-80B for agents).
  - 100-task per-tenant cap with oldest-archived-first GC.
  - Routes: `GET/POST /api/tasks`, `POST /api/tasks/sync`,
    `GET /api/tasks/quota`, `POST /api/tasks/gc`,
    `GET /api/tasks/{id}/dialog`, `POST /api/tasks/{id}/dialog`.

- **Phase B — Intervention loop + orchestrator + writeback**
  - 3-strike intervention engine (`war_room/tasks/intervene.py`).
    Each `comment` returns `{turn_index, agent_replies}`. Turn 3 triggers the
    SemeClaw orchestrator which emits a strict-JSON `{task_patch, rationale,
    dialog_brief}` (with deterministic fallback to `status=needs_review`).
  - Versioned dialogs with `superseded_by` pointer; v(n+1) auto-composed
    after orchestrator decision.
  - Writeback handlers (`war_room/tasks/writeback.py`):
    `paperclip` PATCH, `moltica` PATCH, `local` JSON rewrite, `claude_code`
    read-only by design.
  - Every dialog line carries an `audio_url` of the form
    `/api/tts?text=…&speaker=…&lang=en`.
  - Routes: `POST /api/tasks/{id}/intervene`, `GET /api/tasks/{id}/interventions`.
  - SemeClaw orchestrator agent definition at `war_room/agents/semeclaw.md`.

- **Phase C — Adapter discovery**
  - `GET /api/agents/adapters/{adapter_id}/agents` — discover agents
    inside a connected adapter workspace via env-templated path
    (`{workspace_id}` / `{company_id}` / `{tenant_id}`).
  - `GET /api/agents/adapters/status` per-adapter readiness probe.
  - 404 / 400 / 409+`missing_env` / 502 contract for clean UI error handling.

- **Phase D — Telegram bot + Tasks UI**
  - `POST /api/telegram/webhook` — drive the intervention loop from a
    Telegram chat. Verifies `X-Telegram-Bot-Api-Secret-Token` when
    `TELEGRAM_WEBHOOK_SECRET` is set. Commands: `/list`, `/help`,
    `/comment <task_id> <text>`, plus `<id_prefix>: <text>` shorthand.
    Replies with agent answers and orchestrator decision summary.
  - `GET /tasks` — premium-polish single-page UI:
    - Two-column layout with grouped task list (Needs review / In progress /
      Open / Done), search filter, status pills.
    - Dialog timeline with per-agent gradient avatars, role tags, voice
      play button per line.
    - Intervention thread with turn dots (filled / warning at turn 3),
      orchestrator decision card, JSON patch preview.
    - Sticky composer with Cmd+Enter shortcut, turn counter, contextual hint.
    - Modal-based task creation, toast notifications, skeleton loading,
      empty states with CTA.
    - Inter + JetBrains Mono typography, gradient brand mark.

### Added — OSS positioning

- New `AGENTS.md` at repo root: spec for AI coding agents (Claude Code,
  Codex, Cursor, Aider) to autonomously set up the repo after `git clone`.
- New `cli/doctor.py` with structured `--json` output: connectivity probe
  for DNS, dashboard, Supabase, OpenRouter, DuckDuckGo, every adapter.
- New CLI commands: `semeclaw tasks create/sync/list/dialog/comment/
  interventions/quota/gc`. Every command supports `--json`.
- `scripts/autonomous-setup-prompt.md`: copy-paste prompt for any AI agent.
- README rewritten end-to-end for OSS audience.
- New `docs/TASKS.md` with full lifecycle documentation.

### Fixed

- `cli/doctor.py` Windows cp1252 encoding crash (replaced `→` with `->`).
- `_probe_dashboard` falls back to `/api/agents` when `/version.json` is 404.
- DuckDuckGo HTTP 202 no longer marked as failure.
- `war_room/tasks/inbox/*.json` now gitignored (placeholder retained).
- README license badge corrected from "Proprietary" to MIT.

## [0.7.0] - 2026-04-20

### Added
- 6 external platform adapters: Paperclip, Multica, GitHub, Obsidian, Ollama, Telegram
- Telegram bot integration with command handlers (/run, /status, /board, /link, /help)
- Onboarding module: discovery, seed, sync
- Agent run history and health tracking
- Live comment injection during meetings
- Task-driven meeting system with LLM script generation
- Neural TTS via edge-tts (20+ unique voices)
- ElevenLabs Flash v2.5 premium voice layer
- Docker Compose support
- Fly.io deployment configuration

### Changed
- Migrated from PaperclipBridge to PaperclipAdapter
- Refactored dashboard API endpoints
- Improved meeting template system

### Fixed
- Auth guard improvements
- TTS pipeline reliability
- Startup deduplication
- AdClaw integration stability

## [0.6.0] - 2026-04-15

### Added
- Voice cloning and transcript generation
- Slack integration
- GitHub Action workflow
- Stripe billing scaffold
- pytest suite

## [0.5.0] - 2026-04-10

### Added
- Voice overrides
- Meeting templates
- Cost ledger
- Fly.io deployment support

## [0.4.0] - 2026-04-05

### Added
- Server-Sent Events (SSE)
- NERVIX card integration
- Paperclip adapter
- Theater mode

## [0.3.0] - 2026-03-28

### Added
- Data ingestion pipeline
- Multi-tenant support
- Webhooks
- Metrics endpoint
- Share links
- CI/CD pipeline

## [0.2.0] - 2026-03-20

### Added
- Professional README
- Architecture documentation
- API reference
- Embeddable agent interface

## [0.1.0] - 2026-03-15

### Added
- Initial SemeClaw release
- War Room dashboard
- Basic agent orchestration
- Paperclip bridge
