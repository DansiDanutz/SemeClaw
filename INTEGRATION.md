# SemeClaw Agent — Integration Guide

**Version:** 0.7.0
**Target audience:** Paperclip companies, AI platforms (NERVIX), developers embedding SemeClaw in their product.

---

## What is SemeClaw Agent?

A self-hosted HTTP + embeddable iframe agent that converts any task report (markdown) into a **cinematic multi-agent meeting** with:

- Scripted dialogue (host announcer → orchestrator → 5 specialist agents → Dan)
- Voice per speaker (ElevenLabs Flash v2.5 primary, edge-tts fallback)
- User interjection (2-question budget, live recalibration)
- Task re-analysis on meeting close, with a `VERDICT: CORRECT — proceed` line
- 48h rolling storage + pin-to-save

Three entry points:
1. **HTTP API** — drive it from your backend
2. **iframe embed** — drop into any web page (CMS, dashboard, NERVIX marketplace)
3. **JS widget** — `<script>` + `data-semeclaw-meeting="..."`

---

## Quickstart

### 1. Run SemeClaw (local or server)

```bash
cd SemeClaw
uv run python war_room/dashboard/server.py
# → http://127.0.0.1:8765
```

Or via Docker (Phase 2):

```bash
docker run -p 8765:8765 \
  -e ELEVENLABS_API_KEY=... \
  -e OPENROUTER_API_KEY=... \
  -e SEMECLAW_API_KEY=... \
  -e SEMECLAW_PUBLIC_URL=https://semeclaw.example.com \
  ghcr.io/dansidanutz/semeclaw:0.2.0
```

### 2. Confirm it's alive

```bash
curl http://127.0.0.1:8765/api/agent/manifest | jq
```

You should see a JSON manifest with `capabilities`, `endpoints`, and `auth`.

---

## Integration Option A — HTTP API

### Authentication

If `SEMECLAW_API_KEY` is set, **write endpoints** require:

```
Authorization: Bearer <SEMECLAW_API_KEY>
```

Read endpoints (`GET /api/reports`, `GET /api/meeting/script`, `GET /api/tts`) stay open so embed clients work without exposing the key.

### Read report + generate a meeting audio

```python
import httpx, os

c = httpx.Client(
    base_url="https://semeclaw.example.com",
    headers={"Authorization": f"Bearer {os.environ['SEMECLAW_API_KEY']}"},
)

# 1. List reports
reports = c.get("/api/reports").json()

# 2. Generate meeting audio for one
name = reports[0]["name"]
audio_bytes = c.get("/api/meeting/audio", params={"name": name}).content
open("meeting.mp3", "wb").write(audio_bytes)

# 3. Save (pin) it so it survives the 48h rolling window
c.post("/api/meeting/pin", params={"name": name})

# 4. Later, finalize with Q&A — appends to the report .md + runs a verdict pass
c.post("/api/meeting/finalize", json={
    "name": name,
    "qa_pairs": [
        {"question": "How long does migration take?", "responder": "GSD", "response": "About 6 weeks."}
    ],
    "transcript": [
        {"speaker": "Narrator", "text": "Meeting opens...", "type": "agent"},
        {"speaker": "Dan", "text": "How long...?", "type": "user"},
    ],
})
```

### Pick best agent for a free-form question

```python
r = c.post("/api/meeting/redirect", json={
    "question": "Who owns the backend for the new feature?",
    "attendees": ["Dan", "David", "Dexter", "Memo", "GSD"],
    "history": [],
    "subject": "Feature X kickoff",
})
print(r.json())  # {"responder": "Dexter", "response": "I'll own the backend..."}
```

### Rewrite remaining meeting segments given Q&A

```python
r = c.post("/api/meeting/replan", json={
    "remaining": [...],  # list of {speaker, text, role, pause_ms_after}
    "question": "We dropped the price — does that change the plan?",
    "answer": "Yes, we need to compress the GTM to 4 weeks.",
    "answerer": "GSD",
    "subject": "Q2 pricing shift",
    "attendees": ["Dan", "David", "GSD", "Hermes"],
})
new_segments = r.json()["segments"]
```

---

## Integration Option B — iframe embed

The fastest path. Drop SemeClaw into any web page as an iframe.

### Minimal

```html
<iframe
  src="https://semeclaw.example.com/embed?meeting=ops-review.md&v=2&theme=dark"
  style="width:100%;height:720px;border:0;border-radius:12px"
  allow="autoplay; clipboard-write"
  title="SemeClaw War Room"
></iframe>
```

### Query-param reference

| Param     | Purpose                                      | Default |
|-----------|----------------------------------------------|---------|
| `meeting` | Report filename (e.g. `ops-review.md`)       | —       |
| `v`       | Layout: `1` (flat) or `2` (orbital)          | `1`     |
| `theme`   | `dark` (more themes in future)                | `dark`  |

### Allowing iframe from your domain

Set `SEMECLAW_FRAME_ANCESTORS` on the SemeClaw deployment:

```bash
SEMECLAW_FRAME_ANCESTORS="https://nervix.ai https://*.paperclip.com"
```

---

## Integration Option C — JS widget (`<script>` tag)

Cleaner than iframes when you want multiple meeting embeds on one page.

```html
<!-- Head -->
<script src="https://semeclaw.example.com/embed.js" defer></script>

<!-- Body — each div with data-semeclaw-meeting becomes an embed -->
<div
  data-semeclaw-meeting="quarterly-review.md"
  data-semeclaw-v="2"
  data-semeclaw-theme="dark"
  style="width:100%;height:720px;border-radius:12px"
></div>
```

Auto-mount on `DOMContentLoaded`. For dynamically-added containers:

```js
// after you insert a new <div data-semeclaw-meeting="...">
window.SemeClaw.scan();
```

### Attributes

| Attribute                  | Purpose                          |
|---------------------------|-----------------------------------|
| `data-semeclaw-meeting`   | Report filename                   |
| `data-semeclaw-v`         | `1` or `2`                        |
| `data-semeclaw-theme`     | `dark`                            |
| `style="width; height"`   | Container box — iframe fills it   |

---

## NERVIX Integration (Phase 3 — roadmap)

**Goal:** Each NERVIX marketplace user gets a scoped SemeClaw instance. When a task card's "Convene War Room" button is clicked, a modal opens with the SemeClaw embed for that task's report.

### Flow

1. **User installs the SemeClaw agent card** from the NERVIX marketplace.
2. NERVIX provisions a tenant: calls `POST /api/tenants` on SemeClaw with `{tenant_id, api_key, branding}`.
3. NERVIX stores the returned `SEMECLAW_TENANT_TOKEN` in the user's vault.
4. When the user clicks "Convene War Room" on a task:
   - NERVIX backend posts the task's markdown to `POST /api/reports` (creates it on SemeClaw)
   - NERVIX frontend opens a modal with the SemeClaw iframe: `/embed?meeting=<task-id>.md&v=2`
5. On meeting finalize, SemeClaw POSTs a webhook to NERVIX with the updated markdown + verdict line → NERVIX appends to the task.

### Webhook payload (planned)

```json
{
  "event": "meeting.finalized",
  "tenant_id": "nervix-user-12345",
  "meeting_id": "75f6809d",
  "report_name": "task-abc-2026-04-18.md",
  "qa_count": 2,
  "verdict_line": "VERDICT: CORRECT — proceed",
  "updated_markdown_url": "https://semeclaw.example.com/api/reports/content?name=task-abc-2026-04-18.md",
  "audio_url": "https://semeclaw.example.com/api/meeting/audio?name=task-abc-2026-04-18.md"
}
```

---

## Paperclip Integration (Phase 4 — roadmap)

**Goal:** SemeClaw becomes a first-class Paperclip agent — spawnable from any Paperclip task.

### Adapter shape

```json
{
  "agent_type":  "semeclaw.war-room",
  "name":        "War Room (SemeClaw)",
  "icon":        "🎭",
  "endpoint":    "https://semeclaw.example.com",
  "trigger":     "on_task_comment",
  "input":       {"task_markdown": "<string>"},
  "output":      {"meeting_url": "<string>", "verdict": "<string>"},
  "bridge_file": "war_room/paperclip_bridge.py"
}
```

---

## Environment Variables

Copy `.env.example` and fill in what you use.

| Var                        | Purpose                                                                 |
|---------------------------|--------------------------------------------------------------------------|
| `ELEVENLABS_API_KEY`      | Tier-1 TTS. Falls through to edge-tts if unset.                         |
| `OPENROUTER_API_KEY`      | LLM for redirect/replan/finalize. Falls through to null response if unset. |
| `SEMECLAW_API_KEY`        | Bearer token for write endpoints. Unset = open mode.                    |
| `SEMECLAW_CORS_ORIGINS`   | Allow-list (comma-sep). `*` for anyone. Use explicit list in prod.      |
| `SEMECLAW_FRAME_ANCESTORS`| CSP directive controlling who can iframe.                                |
| `SEMECLAW_TENANT_ID`      | Displayed in manifest + logs                                             |
| `SEMECLAW_PUBLIC_URL`     | External URL baked into `embed.js` + manifest                            |

---

## Observability

- `GET /api/agent/health` — system status including Supabase + Paperclip bridge
- `GET /api/meeting/list` — cached meetings + prune stats
- Response headers `X-SemeClaw-Version`, `X-Speaker`, `X-Voice`, `X-TTS-Engine`

---

## Roadmap

See [SEMECLAW_AGENT_PLAN.md](./SEMECLAW_AGENT_PLAN.md) for the full phased plan.

Phase 1 (this release): Manifest + embed + CORS/auth.
Phase 2: Docker + CI + tagged releases.
Phase 3: NERVIX marketplace card.
Phase 4: Paperclip first-class agent.
Phase 5: Multi-tenant SaaS + metered billing.
