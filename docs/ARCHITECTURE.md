# SemeClaw Architecture

## System Overview

```mermaid
flowchart LR
    subgraph Consumer["Consumer Surface"]
        iframe[🪟 Iframe Embed]
        sdk[📦 JS SDK<br/>embed.js]
        http[🔌 HTTP API<br/>Bearer auth]
    end

    subgraph SemeClaw["SemeClaw Agent :8765"]
        api[FastAPI App]
        mid[Auth + CORS + CSP<br/>middleware]
        skill[Meeting Skill<br/>pure module]
        cache[Audio Cache<br/>48h rolling + saved/]
        db[(Reports<br/>+ meetings<br/>+ scripts)]
    end

    subgraph External["External Services"]
        eleven[🎙 ElevenLabs<br/>Flash v2.5]
        edge[🗣 edge-tts<br/>fallback]
        or[🧠 OpenRouter<br/>Gemini 2.5 Flash]
        supa[(🗄 Supabase<br/>telemetry)]
        paperclip[📎 Paperclip<br/>bridge]
    end

    iframe --> mid
    sdk --> mid
    http --> mid
    mid --> api
    api --> skill
    api --> cache
    api --> db
    skill --> or
    api --> eleven
    api --> edge
    api -.-> supa
    api -.-> paperclip

    classDef ext fill:#1e293b,stroke:#334155,color:#cbd5e1
    classDef core fill:#065f46,stroke:#10b981,color:#ecfdf5
    classDef cons fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    class eleven,edge,or,supa,paperclip ext
    class api,mid,skill,cache,db core
    class iframe,sdk,http cons
```

## Request Flow — "Play a meeting"

```mermaid
sequenceDiagram
    autonumber
    participant U as User/Embed
    participant UI as Dashboard UI
    participant API as FastAPI
    participant Skill as meeting_skill.py
    participant LLM as OpenRouter
    participant TTS as ElevenLabs/edge-tts
    participant FS as Audio Cache

    U->>UI: Click 🔊 Play Meeting
    UI->>API: GET /api/meeting/script?name=...
    API->>Skill: build_script(report, task, meeting_id)
    Skill-->>API: [{speaker, text, pause_ms_after}, ...]
    API-->>UI: script JSON
    loop For each segment
        UI->>API: GET /api/tts?text=...&speaker=...
        API->>TTS: synthesize
        TTS-->>API: audio/mpeg bytes
        API-->>UI: MP3 chunk
        UI->>UI: Play + animate speaker
    end
    Note over U,UI: Dan asks a question → submit
    UI->>API: POST /api/meeting/redirect
    API->>LLM: pick responder + generate answer
    LLM-->>API: {responder, response}
    API-->>UI: agent response
    UI->>API: POST /api/meeting/replan
    API->>LLM: rewrite remaining segments
    LLM-->>API: updated segments
    API-->>UI: new queue
    Note over U,UI: Meeting continues with recalibrated plan
    U->>UI: 🏁 Finish
    UI->>API: POST /api/meeting/finalize
    API->>FS: append Q&A to report.md
    API->>LLM: re-analyze with Q&A
    LLM-->>API: updated analysis + verdict
    API->>FS: write verdict line
    API-->>UI: done
```

## Storage Model

```
war_room/
├── research/
│   ├── saved/                     ← pinned reports, never deleted
│   │   └── <task-slug>.md
│   └── <task-slug>.md             ← rolling, 48h TTL
├── audio/
│   ├── meetings/
│   │   ├── saved/                 ← pinned audio, never deleted
│   │   │   └── <meeting_id>_<slug>.mp3
│   │   └── <meeting_id>_<slug>.mp3  ← rolling, 48h TTL
│   ├── scripts/                   ← cached translated scripts (future)
│   └── segments/                  ← per-speaker segment cache (future)
└── logs/
    ├── dashboard.log
    └── dashboard-err.log
```

Retention is enforced by a background task (`_prune_old()`) that runs hourly and also on every `/api/meeting/list` + `/api/reports` call. Files under `saved/` are bypassed.

## Meeting Script Pipeline

```mermaid
flowchart TD
    A[Report .md] -->|parse_report| B[Sections by ## heading]
    B --> C[Route each section<br/>to a speaker]
    C --> D[Build script:<br/>Host announcement<br/>→ Orchestrator opens<br/>→ For each section: handoff + speaker<br/>→ Orchestrator closes<br/>→ Dan adjourns]
    D --> E[Return MeetingScript:<br/>meeting_id, subject,<br/>attendees, segments]
    E --> F{Lang == 'en'?}
    F -->|Yes| G[Return as-is]
    F -->|No| H[Translate each segment<br/>via OpenRouter]
    H --> G
```

The skill is a **pure module** with no HTTP dependencies — unit-testable, reusable from CLI, CLI, Slack bot, etc.

## Auth Model

```
┌──────────────────────────────────────────────────┐
│ SEMECLAW_API_KEY unset      → open mode (dev)    │
│ SEMECLAW_API_KEY set        → bearer required    │
│                                on WRITE endpoints│
└──────────────────────────────────────────────────┘

READ endpoints (always open):
  GET /api/agent/manifest
  GET /api/agent/health
  GET /api/reports
  GET /api/reports/content
  GET /api/meeting/script
  GET /api/meeting/audio
  GET /api/meeting/list
  GET /api/tts
  GET /embed
  GET /embed.js

WRITE endpoints (bearer when key set):
  POST /api/meeting/pin
  POST /api/meeting/unpin
  POST /api/meeting/finalize
  POST /api/meeting/replan
  POST /api/meeting/redirect
```

Reads stay open so iframe embeds work without exposing the key to the browser. Writes are protected so only trusted servers can mutate state.

## Layout V1 vs V2

### V1 — Flat (default)

```
┌──────────────────────────────────────────────────┐
│  MEETING XXX — TASK SUBJECT     🇺🇸 🔊 ⏳ 📋 ✕  │
├──────────────────────────────────────────────────┤
│  👤 Dan   🏛 David   📐 GSD  ✍️ Hermes  🔬 ...   │  ← avatars flex-row
├──────────────────────────────────────────────────┤
│  Speaker ▸ ▯▯▯▯▯▯▯▯                    🔊 Voice │  ← waveform
├──────────────────────────────────────────────────┤
│  💬 Narrator: This meeting is number...          │  ← transcript
│  💬 David:    Thanks. Let's dig in...            │
│  💬 GSD:      My take is...                      │
│                                                  │
├──────────────────────────────────────────────────┤
│  💬 [Ask up to 2 questions…]  Send(2)   🏁Finish│
└──────────────────────────────────────────────────┘
```

### V2 — Orbital (opt-in)

```
┌──────────────────────────────────────────────────┐
│  MEETING XXX                   🇺🇸 🔊 ⏳ 📋 ✕   │
├──────────────────────┬───────────────────────────┤
│       ◉ THE ROOM     │ 💬 Narrator: This meeting │
│                      │    is number 75f6809d...  │
│         👤 Dan       │                           │
│                      │ 🏛 David: Thanks for the  │
│   🔬          🏛     │    intro. Let's get into  │
│ Autores      David   │    it.                    │
│                      │                           │
│        ┌─────┐       │ 📐 GSD: Based on current  │
│        │ ◉   │       │    public info, NERVIX    │
│        │David│       │    isn't a widely recog…  │
│        └─────┘       │                           │
│   ✍️          📐     │                           │
│ Hermes        GSD    │                           │
│                      │                           │
│         🧪 Auto      │                           │
│                      │                           │
│          ← Back V1   │ 💬 [...] Send(2) 🏁Finish │
└──────────────────────┴───────────────────────────┘
```

Agents arranged in a 360° ring. The center **LIVE SPEAKER card** morphs in real-time to show the current speaker's emoji, name, and color.

## Tech Stack

| Layer | Tech |
|-------|------|
| Runtime | Python 3.10+ minimum, 3.13 recommended + uvicorn |
| Web framework | FastAPI |
| Frontend | Vanilla HTML + JS (no build step) |
| Voice TTS | ElevenLabs Flash v2.5 → edge-tts fallback |
| Audio concat | ffmpeg |
| LLM | OpenRouter (Gemini 2.5 Flash) |
| Storage | Filesystem (for meetings + reports) |
| Telemetry (opt) | Supabase |
| Deploy | Docker + launchd (dev) |
