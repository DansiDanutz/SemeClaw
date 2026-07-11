# SemeClaw Agent — Claude Code Project Brief

> If you're an AI agent (Claude Code, Cursor, etc.) picking up this repo, read this first.

## What it is

SemeClaw is an open-claw-style agent system. Its headline surface is the **War Room**: a dashboard at `http://127.0.0.1:8765` that converts any task report (markdown) into a cinematic multi-agent meeting with voice, user interjections, live recalibration, and automatic task re-analysis.

**Strategic direction:** turn it into a standalone **embeddable agent** consumable by Paperclip companies and AI platforms (NERVIX). See `SEMECLAW_AGENT_PLAN.md` for the phased roadmap and `INTEGRATION.md` for the public contract.

## Architecture at a glance

```
SemeClaw/
├── src/semeclaw/          ← personal AI brain (chat, tools, memory, skills)
│   ├── core/              ← agent loop
│   ├── channel/           ← CLI, Telegram, Discord, WebSocket
│   ├── provider/          ← LLM adapters (litellm)
│   ├── server/            ← HTTP server
│   ├── tools/             ← registry + builtins
│   └── utils/
├── war_room/              ← MONETIZABLE surface — the Meeting Room agent
│   ├── dashboard/
│   │   ├── server.py      ← FastAPI app, all public endpoints live here
│   │   ├── index.html     ← UI (v1 flat + v2 orbital)
│   │   └── meeting_skill.py ← pure module: report → scripted segments
│   ├── research/          ← rolling reports (48h retention, saved/ for pinned)
│   ├── audio/             ← cached meeting MP3s (48h + saved/)
│   ├── logs/              ← runtime logs
│   ├── agents/            ← agent cards
│   ├── paperclip_bridge.py
│   └── auto_scheduler.py
├── default_workspace/
├── Dockerfile             ← Phase 2 deploy
├── .env.example
├── INTEGRATION.md         ← public agent contract
└── SEMECLAW_AGENT_PLAN.md ← roadmap
```

## Public API (read INTEGRATION.md for full spec)

- `GET  /api/agent/manifest` — capabilities + endpoints + auth info
- `GET  /api/agent/health`
- `GET  /api/reports`
- `GET  /api/reports/content?name=`
- `GET  /api/meeting/script?name=`
- `GET  /api/meeting/audio?name=`
- `POST /api/meeting/redirect` — pick agent to answer a question
- `POST /api/meeting/replan` — rewrite remaining segments given Q&A
- `POST /api/meeting/finalize` — append Q&A + verdict pass to the source report
- `POST /api/meeting/pin` / `/unpin`
- `GET  /api/tts?text=&speaker=&lang=en`
- `GET  /voice-builder` — no-code Voice Agent Builder UI
- `GET/POST /api/voice-agents` + `POST /api/voice-agents/{id}/respond` — voice agent CRUD + chat turn
- `GET  /assistant` — Digital Assistant UI (personal assistant: memory, missions, Twilio calls)
- `POST /api/assistant/message` / `/end` / `/call` + `GET /api/assistant/memory?q=` — assistant API
- `GET  /embed?meeting=&v=1&theme=dark` — iframe-ready page
- `GET  /embed.js` — `<script>`-tag JS SDK

## Key design decisions

1. **V1 is default** (flat compact roster). **V2** is the cinematic orbital layout — toggle via "🎭 Switch to V2 Room" button.
2. **English only** for voice. Multi-lingual was disabled after quality issues.
3. **2-question budget** per meeting with counter on Send button. After 2 → "🔒 No more questions".
4. **Finish button** in chat bar — kills audio, appends Q&A to source `.md`, runs a verdict pass (`VERDICT: CORRECT — proceed`).
5. **48h retention** for both meetings and reports. Pin to save forever.
6. **ElevenLabs Flash v2.5** is primary TTS. **edge-tts** is fallback (covers non-English + when ElevenLabs is down).
7. **Dan's voice = Brian** (Deep, Resonant, Comforting — American entrepreneur).
8. **Auth model**: bearer-token on WRITE endpoints only (finalize, pin, replan, redirect). Reads stay open so iframe embeds work without exposing the key.

## How to run

```bash
# One-off
uv run python war_room/dashboard/server.py

# As a service (Mac) — already wired via launchd
launchctl load ~/Library/LaunchAgents/com.danslab.war-room-dashboard.plist

# In Docker (Phase 2)
docker run -p 8765:8765 --env-file .env ghcr.io/dansidanutz/semeclaw:0.2.0
```

## Common tasks for agents working on this repo

- **Add a new public capability** → extend `server.py` + list in `api_agent_manifest()` + document in `INTEGRATION.md`.
- **Change voice mapping** → `_ELEVEN_VOICES` dict near the top of `server.py`.
- **Tune meeting pacing** → `meeting_skill.py` (pause_ms_after defaults) + `_tmV2Enabled` logic in `index.html`.
- **Harden auth** → `_semeclaw_auth_and_csp` middleware at the top of `server.py`, controlled by `SEMECLAW_API_KEY` env.
- **Ship new embed theme** → `/embed` route honours `theme` param; add CSS hooks in `index.html` keyed off `data-theme`.

## What NOT to do

- Don't hardcode secrets — use `.env` (gitignored). `.env.example` is the contract.
- Don't break the 48h retention for unsaved meetings — it's a design commitment.
- Don't delete `meeting_skill.py` imports — it's what makes the whole meeting-as-a-scripted-conversation work.
- Don't remove the V1 fallback — some users will always want the compact layout.

## Links

- **Roadmap:** `SEMECLAW_AGENT_PLAN.md`
- **Integration:** `INTEGRATION.md`
- **NERVIX vision:** `war_room/NERVIX_VISION.md`
