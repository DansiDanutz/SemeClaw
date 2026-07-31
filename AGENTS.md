# AGENTS.md — How AI coding agents should set up SemeClaw

> **You are an AI coding agent (Claude Code / Codex / Cursor / Aider / ...).**
> A human just cloned this repo and asked you to "make it work."
> This file is your spec. Follow it top to bottom. Every step is verifiable.

SemeClaw is an open-source War Room: a SemeClaw Orchestrator agent that runs
meeting rooms with 4+ specialist agents (Research, Writer, Scraping, Coder)
plus pluggable adapters to Paperclip, Moltica, Claude Code, and GPT.

The whole CLI is **agent-native**: every command supports `--json` so you can
parse it and act on it without scraping prose.

---

## 0. Hard rules (do not violate)

1. **Never commit secrets.** `.env` is gitignored. Use `~/.semeclaw/env` (chmod 600) for user keys.
2. **Never ask the human for a password.** OAuth / SSO only.
3. **The system must work without any API keys.** Free fallbacks exist for every component:
   - Dialog generation: deterministic templates if `OPENROUTER_API_KEY` is unset.
   - Web search: DuckDuckGo HTML if no Brave / SearXNG.
   - TTS: edge-tts (Microsoft free) if no ElevenLabs.
4. **Do not run `git push`, `fly deploy`, or `vercel deploy` without explicit human confirmation.**

---

## 1. Setup checklist (run in order)

### 1a. System dependencies
```bash
python --version            # need >= 3.10
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev
```

Do not install the optional `crewai` extra on a network-exposed deployment
until its pinned ChromaDB dependency has a patched release. The native engine
is the supported production default.

### 1b. Local env
```bash
cp -n .env.example .env     # never overwrite an existing .env
```
Then for each unset key in `.env` that the human cares about, ask them
**once** which they want to set. Defaults work without any of them.

### 1c. Verify
```bash
python -m cli doctor --json > /tmp/doctor.json
```
Parse `/tmp/doctor.json`. The shape is:
```json
{
  "ok": true,
  "hard_fail": false,
  "checks": [
    {"group": "...", "name": "...", "ok": true|false,
     "detail": "...", "hint": "...", "required": true|false}
  ]
}
```
- If `hard_fail: true` → fix every `required: true, ok: false` check before continuing. The `hint` field tells you exactly what to do.
- If `hard_fail: false` but some `ok: false` → those are optional. Tell the human what they unlock and ask whether to set them up.

### 1d. Apply database migrations before deployment (only if Supabase is configured)
If `DLS_TEAM_SUPABASE_URL` is set in `.env`, apply these migrations in order:

1. `war_room/db/migrations/2026_04_23_adclaw_00_base.sql`
2. `war_room/db/migrations/2026_04_24_adclaw_projects_and_spotlight.sql`
3. `war_room/db/migrations/2026_04_23_adclaw_01_subscription_columns.sql`
4. `war_room/db/migrations/2026_04_23_adclaw_tier.sql`
5. `war_room/db/migrations/2026_04_23_adclaw_credits.sql`
6. `war_room/db/migrations/2026_04_23_adclaw_deduct_backfill.sql`
7. `war_room/db/migrations/2026_04_23_adclaw_special.sql`
8. `war_room/db/migrations/2026_04_23_adclaw_weekly_bonus.sql`
9. `war_room/db/migrations/2026_04_24_semeclaw_tasks.sql`
10. `war_room/db/migrations/2026_07_31_adclaw_idempotency.sql`

Apply them through the Supabase SQL editor (safest, human-supervised) or the
Supabase migration tool with explicit human authorization. Before deploying,
run this probe in the SQL editor:

```sql
select
  to_regprocedure('public.adclaw_grant_subscription_invoice_credits(text,uuid,text,integer,timestamp with time zone)') is not null
    as invoice_grant_ready,
  to_regprocedure('public.adclaw_generate_card_once(uuid,uuid,uuid,integer,jsonb)') is not null
    as paid_generation_ready;
```

Both values must be `true`. Then set the GitHub repository variable
`ADCLAW_IDEMPOTENCY_MIGRATION_APPLIED=2026_07_31`; tagged and daily Fly
deployments intentionally fail closed without that acknowledgement.

Probe task storage separately with:
`curl -s ${SEMECLAW_API:-http://127.0.0.1:8765}/api/tasks/quota`.
A 200 with `{"ok": true, ...}` means the task tables exist.

### 1e. Start the dashboard (if not already running)
```bash
semeclaw war-room &           # binds :8765
sleep 2
curl -s http://127.0.0.1:8765/api/agents | head -c 200
```

### 1f. Smoke test
```bash
semeclaw doctor --json | jq '.ok'           # expect: true
semeclaw tasks sync                         # expect: ok=true
semeclaw tasks list --json | jq '.count'    # expect: integer
```

---

## 2. Where things live

| Concern | Path |
|---|---|
| Agent definitions (markdown w/ YAML frontmatter) | `war_room/agents/*.md` |
| Agent registry loader | `war_room/agents/_registry.py` |
| Web search (Brave → SearXNG → DDG) | `war_room/agents/_browser_search.py` |
| Web scraping | `war_room/agents/_scraper.py` |
| Task ingest + dialog + retention | `war_room/tasks/` |
| REST routes | `war_room/dashboard/routes/*.py` |
| Server entry | `war_room/dashboard/server.py` |
| CLI (stdlib, no deps) | `cli/` |
| Typer surface (also exposes `cli/`) | `src/semeclaw/cli/main.py` |
| DB migrations | `war_room/db/migrations/*.sql` |
| Demo task | `demo/tasks/four-agent-live-demo.md` |

---

## 3. Adding a new agent

1. Drop a `<id>.md` into `war_room/agents/` with YAML frontmatter:
   ```yaml
   ---
   id: my_agent
   name: My Agent
   role: One-line job description
   keywords: [keyword, list]
   core: false                    # true = always loaded with the OSS demo
   model_preference:              # tried top-down
     - openrouter:meta-llama/llama-3.3-70b-instruct:free
   tools: []                      # optional — names referenced by registry consumers
   ---
   # Markdown body = system prompt
   ```
2. Restart the server. `/api/agents` will list it automatically.

## 4. Adding a new task source (adapter)

1. Add an async generator to `war_room/tasks/sources.py`:
   ```python
   async def myco_tasks() -> AsyncIterator[dict]:
       base = os.environ.get("MYCO_BASE_URL", "")
       key  = os.environ.get("MYCO_API_KEY", "")
       if not (base and key):
           return
       # ... yield dicts shaped like the contract at the top of sources.py
   ```
2. Register it in the `SOURCES` dict at the bottom of that file.
3. Document creds in `.env.example` and add a probe row in `cli/doctor.py::_probe_adapters`.

## 5. Adding a new API route

1. Create `war_room/dashboard/routes/<feature>.py` with `router = APIRouter(...)`.
2. Mount it in `war_room/dashboard/server.py` next to the others.
3. Add a `cli/<feature>.py` if it's user-facing. Always support `--json`.

---

## 6. Intervention loop (Phase B)

The full task lifecycle is:

```
sync -> dialog v1 -> [user comment x1] -> agent replies
                  -> [user comment x2] -> agent replies
                  -> [user comment x3] -> agent replies + ORCHESTRATOR DECIDES
                                          -> task patched
                                          -> dialog v2 composed
                                          -> writeback to source (paperclip/moltica/local)
                  -> [user comment x1 on v2] -> ...
```

API:
- `POST /api/tasks/{id}/intervene` body `{"comment": "..."}` → returns `{turn_index, agent_replies, orchestrator_decision?, new_dialog?, writeback?}`
- `GET  /api/tasks/{id}/interventions` → all interventions on the latest dialog

CLI:
- `semeclaw tasks comment <task_id> "your comment"` (add `--json` for machine-readable)
- `semeclaw tasks interventions <task_id>`

Orchestrator decision contract (strict JSON, see `war_room/agents/semeclaw.md`):
```json
{
  "task_patch": {"title": "...", "description": "...",
                 "assigned_agents": ["..."], "status": "in_progress|needs_review|done"},
  "rationale":     "1-2 sentences for the audit log",
  "dialog_brief":  "seed prompt for dialog v2"
}
```
Without `OPENROUTER_API_KEY`, the orchestrator falls back to a deterministic
patch that moves status to `needs_review` and appends the latest comment.

TTS: every dialog line carries an `audio_url` of the form
`/api/tts?text=...&speaker=<agent_id>&lang=en` — the frontend just plays it.

Writeback handlers (`war_room/tasks/writeback.py`):
| source       | behavior |
|--------------|----------|
| paperclip    | `PATCH {PAPERCLIP_BASE_URL}/api/tasks/{source_id}` |
| moltica      | `PATCH {MOLTICA_BASE_URL}/v1/tasks/{source_id}` |
| local        | rewrites `war_room/tasks/inbox/{source_id}.json` |
| claude_code  | read-only by design — no writeback |

## 7. Testing strategy

- **Offline**: `python -c "import asyncio; from war_room.tasks.dialog import compose_dialog; ..."` — composer must work with no network.
- **Live**: `semeclaw doctor --json` — must return `{"hard_fail": false}` against any reachable deployment.
- **End-to-end**: drop a `.json` file into `war_room/tasks/inbox/`, run `semeclaw tasks sync`, then `semeclaw tasks dialog <id>` — should auto-generate a 6-line dialog.

## 8. When you finish

Tell the human exactly one of:
- **"All checks green, ready to use. Try `semeclaw tasks sync`."**
- **"Setup partially complete. The following optional features are off: [list]. Want me to set them up?"**
- **"Setup blocked on [exact thing]. I cannot proceed without [resource]."**

Do not generate a wall of text. The human chose an AI agent so they would not have to read.
