# Tasks, dialogs, and the intervention loop

This is the heart of SemeClaw. Read this if you want to understand:
- How tasks get into the system
- How agents dialog about them
- How a human (or another agent) intervenes
- How the orchestrator finalises a decision and writes it back

> Tip: every endpoint described here is also exercised by
> [`cli/tasks.py`](../cli/tasks.py), so a quick `semeclaw tasks <cmd> --help`
> doubles as a runnable contract.

---

## The lifecycle

```
sync       ─►  task ingested from adapter (paperclip / moltica / local / …)
                │
dialog v1  ◄─  composed automatically the first time you GET /api/tasks/{id}/dialog
                │
intervene  ─►  POST /api/tasks/{id}/intervene { "comment": "..." }
                │  turn 1 → agent_replies
                │  turn 2 → agent_replies
                │  turn 3 → agent_replies + 🧭 orchestrator_decision
                │              ├── task patched (status / description / agents)
                │              ├── dialog v2 composed
                │              └── writeback to source system
                │
intervene  ─►  same again, on dialog v2…   (limit resets per version)
```

Three is the default cap on interventions per dialog version. After that
the only way forward is `POST /api/tasks/{id}/dialog` (manual regenerate).
Set `MAX_INTERVENTIONS` in `war_room/tasks/intervene.py` if you want a
different ceiling.

---

## Data model (Supabase)

| Table | Purpose | Key columns |
|---|---|---|
| `semeclaw_tasks` | One row per task per tenant. | `id`, `tenant_id`, `source`, `source_id`, `title`, `description`, `status`, `assigned_agents`, `meta`, `ingested_at`, `archived_at` |
| `semeclaw_dialogs` | Versioned multi-agent meeting per task. | `id`, `task_id`, `version`, `lines` (jsonb), `superseded_by` |
| `semeclaw_interventions` | One row per user comment. | `id`, `dialog_id`, `task_id`, `turn_index`, `user_comment`, `agent_replies`, `orchestrator_decision` |

The migration ships at `war_room/db/migrations/2026_04_24_semeclaw_tasks.sql`.
Apply it via the Supabase SQL editor (safest) or the Supabase MCP if your
agent has access. Without Supabase, the system runs in degraded mode —
every operation returns an error but the server stays up.

---

## Composing a dialog

`war_room/tasks/dialog.py::compose_dialog(task)` returns six lines:

```
[
  { "agent_id": "semeclaw", "role": "orchestrator", "text": "...", "audio_url": "/api/tts?...", "ts": "..." },
  { "agent_id": "research", "role": "specialist",   "text": "...", "audio_url": "/api/tts?..." },
  { "agent_id": "writer",   "role": "specialist",   "text": "...", "audio_url": "/api/tts?..." },
  ...
]
```

Without `OPENROUTER_API_KEY` it uses deterministic templates (so you can
run the whole demo offline). With a key, each assigned agent generates a
single line via its `model_preference` ladder (defined in the agent's
markdown frontmatter).

---

## The orchestrator contract

When the third comment lands, the SemeClaw orchestrator (definition at
`war_room/agents/semeclaw.md`) is asked for a strict-JSON decision:

```json
{
  "task_patch": {
    "title": "...",
    "description": "...",
    "assigned_agents": ["research", "writer"],
    "status": "in_progress | needs_review | done"
  },
  "rationale":     "1-2 sentences for the audit log",
  "dialog_brief":  "seed prompt for dialog v2"
}
```

The call uses OpenRouter's `response_format: json_object` mode for strict
output. If the call fails or no key is configured, a deterministic fallback
fires:
- status → `needs_review`
- description → original + `\n\n[v2 update] <latest comment>`
- rationale → `"3 interventions reached; staging for human review."`

This guarantees the loop **always closes**, even offline.

---

## Writeback

After the patch is applied, `war_room/tasks/writeback.py::push_to_source(task)`
sends the patch back to the source system:

| Source | Behaviour |
|---|---|
| `paperclip` | `PATCH {PAPERCLIP_BASE_URL}/api/tasks/{source_id}` with bearer token |
| `moltica`   | `PATCH {MOLTICA_BASE_URL}/v1/tasks/{source_id}` with bearer token |
| `local`     | Rewrites `war_room/tasks/inbox/{source_id}.json` |
| `claude_code` | Read-only — no writeback |

Failures are surfaced in the `writeback` field of the intervene response
but do **not** block the loop. The task patch and dialog v2 still land.

---

## The HTTP surface

| Method · Path | What it does |
|---|---|
| `GET /api/tasks?limit=&status=` | List tasks for the current tenant. |
| `POST /api/tasks` | Create a manual task. Body: `{title, description?, assigned_agents?, source_id?}`. |
| `POST /api/tasks/sync` | Pull from every configured adapter. Returns `{synced, by_source}`. |
| `GET /api/tasks/{id}` | Fetch a single task. |
| `GET /api/tasks/{id}/dialog` | Latest dialog (auto-composes v1 on first call). |
| `POST /api/tasks/{id}/dialog` | Force regenerate (bumps version). |
| `POST /api/tasks/{id}/intervene` | Add a comment. Body: `{"comment": "..."}`. |
| `GET /api/tasks/{id}/interventions` | All interventions on the latest dialog. |
| `GET /api/tasks/quota` | Tenant quota: active / archived / cap. |
| `POST /api/tasks/gc` | Run retention now (archives oldest beyond cap). |

---

## The CLI surface

```
semeclaw tasks sync                            # POST /api/tasks/sync
semeclaw tasks list [--json]                   # GET  /api/tasks
semeclaw tasks create "<title>"                # POST /api/tasks
semeclaw tasks dialog <id>                     # GET  /api/tasks/{id}/dialog
semeclaw tasks comment <id> "<comment>"        # POST /api/tasks/{id}/intervene
semeclaw tasks interventions <id>              # GET  /api/tasks/{id}/interventions
semeclaw tasks quota                           # GET  /api/tasks/quota
semeclaw tasks gc                              # POST /api/tasks/gc
```

Every command accepts `--json` for AI-agent-friendly machine output.

---

## The Telegram surface

`POST /api/telegram/webhook` accepts standard Telegram Bot API updates and
parses these grammars:

```
/help                           → usage card
/list                           → 10 most-recent tasks
/comment <task_id> <text>       → intervene on a task
<task_id_prefix>: <text>        → same, shorthand (8+ char prefix)
```

Reply messages summarise the agent answers and, on turn 3, the orchestrator
decision + new dialog version.

To wire it up:

```bash
fly secrets set TELEGRAM_BOT_TOKEN=… TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 16)
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR.HOST/api/telegram/webhook&secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

---

## The UI

`GET /tasks` — single-page React-free SPA. Three columns of state:

1. **Sidebar** — tasks grouped by status with a search filter.
2. **Task header** — title, status pill, source, assigned agents, full id.
3. **Cards** — Description · Dialog (with per-line voice play button) ·
   Interventions (turn dots, orchestrator decision card, JSON patch preview)
   · Composer (Cmd+Enter to send, contextual hint, turn counter).

Manual create lives behind the **+ New Task** button (modal).

---

## Adding your own task source

1. Add an async generator to `war_room/tasks/sources.py`:

   ```python
   async def myco_tasks() -> AsyncIterator[dict]:
       base = os.environ.get("MYCO_BASE_URL", "")
       key  = os.environ.get("MYCO_API_KEY", "")
       if not (base and key):
           return
       async with httpx.AsyncClient(base_url=base, timeout=20.0) as c:
           r = await c.get("/v1/tasks", headers={"Authorization": f"Bearer {key}"})
           for row in r.json().get("tasks", []):
               yield {
                   "source": "myco",
                   "source_id": row["id"],
                   "title": row["title"],
                   "description": row.get("body"),
                   "status": row.get("status", "open"),
                   "assigned_agents": [],
                   "meta": {"raw": row},
               }
   ```

2. Register it in the `SOURCES` dict at the bottom of `sources.py`.
3. Document the env vars in `.env.example` and add a probe row in
   `cli/doctor.py::_probe_adapters`.
4. (Optional) For writeback: add a handler in `war_room/tasks/writeback.py`
   and register it in `_DISPATCH`.

That's it — `semeclaw tasks sync` will start pulling from your source
on next call.
