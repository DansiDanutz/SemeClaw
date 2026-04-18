# SemeClaw API Reference

**Base URL:** `http://127.0.0.1:8765` (local) or your deployed host.
**Version:** 0.2.0
**Auth:** Bearer token on WRITE endpoints when `SEMECLAW_API_KEY` is set.

---

## Agent metadata

### `GET /api/agent/manifest`
Returns capabilities, endpoints, auth requirements, and tenant info.

**Response** — `200 OK`
```json
{
  "id": "semeclaw-war-room",
  "name": "SemeClaw War Room",
  "version": "0.2.0",
  "tenant": "default",
  "public_url": "http://127.0.0.1:8765",
  "description": "Cinematic AI agent meeting room...",
  "capabilities": ["meeting.script", "meeting.audio", "meeting.redirect",
                   "meeting.replan", "meeting.finalize", "meeting.pin",
                   "reports.list", "reports.content", "tts.synthesize",
                   "embed.iframe", "embed.widget"],
  "endpoints": { "...": "..." },
  "auth": {
    "required_for_writes": true,
    "scheme": "bearer",
    "header": "Authorization: Bearer <SEMECLAW_API_KEY>",
    "protected_paths": ["/api/meeting/pin", "..."]
  },
  "tts": { "engine": "elevenlabs-flash-v2.5 + edge-tts fallback",
           "languages": ["en"], "voice_map_size": 30 },
  "retention": { "meetings_hours": 48, "reports_hours": 48, "pin_to_save": true },
  "layouts": ["v1-flat", "v2-orbital"],
  "meeting_budget": {
    "max_user_questions_per_meeting": 2,
    "recalibration": "orchestrator/hermes",
    "finalize_verdict_line": true
  },
  "integrations": { "paperclip": true, "nervix": "planned-phase-3" }
}
```

### `GET /api/agent/health`
Simple liveness + downstream status check.

---

## Reports

### `GET /api/reports`
List all reports (rolling + saved). Prunes >48h unsaved reports before responding.

**Response** — array of:
```json
{
  "name": "ops-review-2026-04-18.md",
  "saved": true,
  "size": 1938,
  "modified": "2026-04-18T03:46:04.000000",
  "preview": "# War Room Report\n**Task:** ..."
}
```

### `GET /api/reports/content?name={name}`
Return the full markdown content of a report.

**Response** — `200 OK`
```json
{
  "name": "ops-review-2026-04-18.md",
  "saved": true,
  "content": "# War Room Report\n\n**Task:** ..."
}
```

**Errors** — `404` if not found.

---

## Meetings

### `GET /api/meeting/script?name={name}&lang=en`
Convert a report into a scripted meeting — announcer, orchestrator, agents, Dan.

**Response**
```json
{
  "meeting_id": "75f6809d",
  "subject": "Quick test: what is NERVIX?",
  "attendees": ["Dan", "David", "Autoresearch", "GSD", "Hermes"],
  "lang": "en",
  "segments": [
    {
      "speaker": "Narrator",
      "text": "This meeting is number 75f6809d. Subject: ...",
      "role": "host",
      "pause_ms_after": 450
    },
    {
      "speaker": "David",
      "text": "Thanks. Welcome to the table. Today's question...",
      "role": "orchestrator",
      "pause_ms_after": 300
    }
  ]
}
```

### `GET /api/meeting/audio?name={name}&download=false`
Generate + cache the concatenated meeting MP3.

- First call: builds (takes ~5-15s depending on segment count + TTS latency)
- Subsequent calls: serves from cache instantly
- `download=true` → adds `Content-Disposition: attachment`

**Response** — `audio/mpeg` binary.

### `GET /api/meeting/list`
List all cached meeting MP3s (rolling + saved). Runs prune.

### `POST /api/meeting/pin?name={name}` 🔐
Pin both the report `.md` and its cached meeting `.mp3` to `saved/` so they survive the 48h cleanup.

### `POST /api/meeting/unpin?name={name}&file={file}` 🔐
Reverse of pin.

### `POST /api/meeting/redirect` 🔐
When a user (Dan) interjects with a question, pick the best agent to answer + generate their response.

**Body**
```json
{
  "question": "How long will the migration take?",
  "attendees": ["Dan", "David", "GSD", "Dexter"],
  "history": [{"speaker":"GSD","text":"Budget is healthy"}],
  "subject": "Platform migration"
}
```

**Response**
```json
{
  "responder": "Dexter",
  "response": "Based on the complexity, about six weeks including buffer."
}
```

### `POST /api/meeting/replan` 🔐
Given the remaining segments + Dan's question + the agent's answer, rewrite the remaining turns to incorporate the new context.

**Body**
```json
{
  "remaining": [{"speaker":"GSD","text":"...","role":"agent","pause_ms_after":300}],
  "question": "We dropped the price — does that change the plan?",
  "answer": "Yes, compress the GTM to 4 weeks.",
  "answerer": "GSD",
  "subject": "Q2 pricing shift",
  "attendees": ["Dan","David","GSD","Hermes"]
}
```

**Response**
```json
{
  "segments": [{"speaker":"...","text":"revised line","role":"agent","pause_ms_after":300}],
  "changed": true
}
```

### `POST /api/meeting/finalize` 🔐
End-of-meeting: append Q&A to the source report `.md`, run a verification pass, add an "Updated Analysis" + `VERDICT:` line.

**Body**
```json
{
  "name": "ops-review-2026-04-18.md",
  "transcript": [{"speaker":"Narrator","text":"...","type":"agent"}],
  "qa_pairs": [{"question":"...","responder":"GSD","response":"..."}]
}
```

**Response**
```json
{
  "ok": true,
  "updated": true,
  "verdict_line": "VERDICT: CORRECT — proceed",
  "qa_count": 2
}
```

---

## Text-to-Speech

### `GET /api/tts?text={text}&speaker={speaker}&lang=en`
Stream MP3 audio for a given text + speaker.

- `lang=en` routes to ElevenLabs Flash v2.5 if `ELEVENLABS_API_KEY` set
- Non-English, non-mapped speakers, or ElevenLabs failure → falls through to edge-tts

**Response** — `audio/mpeg`

**Response headers**
- `X-Speaker` — the requested speaker
- `X-Voice` — resolved voice name (e.g. `Brian`)
- `X-TTS-Engine` — `elevenlabs-flash-v2.5` or `edge-tts`

---

## Embed

### `GET /embed?meeting={name}&v=1&theme=dark`
Serve the dashboard HTML configured for iframe embedding.

### `GET /embed.js`
Drop-in JS widget. Auto-scans for `<div data-semeclaw-meeting="...">` and injects an iframe.

### `GET /embed/manifest.json`
Widget manifest describing supported attributes.

---

## WebSocket

### `WS /ws`
Real-time event stream for the War Room dashboard:
- Task run completions
- New reports
- Paperclip board state changes
- Agent health changes

(Consumer SSE planned for Phase 2+ — see `docs/ENHANCEMENTS.md`.)

---

## Legend

🔐 = Write endpoint, requires `Authorization: Bearer <SEMECLAW_API_KEY>` when the env var is set.
