# SemeClaw

**The AI Brain of Dan's Lab** — A production-ready AI agent system built for research, ideation, and project management powering [NERVIX](https://nervix.ai).

Built from scratch following the [build-your-own-openclaw](https://github.com/czl9707/build-your-own-openclaw) tutorial, SemeClaw implements all 18 steps from basic chat to a full multi-agent orchestration platform.

## What It Does

SemeClaw is a self-hosted AI agent system that runs on your machine. It connects to Claude (Anthropic) and gives you a personal AI brain with tools, memory, skills, and multi-platform access.

- **Chat** — Conversational interface powered by Claude via [litellm](https://docs.litellm.ai/)
- **Tools** — Read/write files, run shell commands, search the web, extract web pages
- **Skills** — Extend capabilities with markdown-based skill definitions
- **Memory** — Long-term knowledge storage across topics, projects, and daily notes
- **Multi-Agent** — Seme (primary brain) delegates to Cookie (memory manager)
- **Multi-Platform** — CLI, Telegram, Discord, WebSocket
- **Cron Jobs** — Scheduled tasks for automated research and notifications
- **Context Management** — Auto-compaction when conversations get long

## Quick Start

```bash
# 1. Install uv (fast Python package manager)
pip install uv

# 2. Set up your config
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml — add your Anthropic API key

# 3. Run SemeClaw
uv run semeclaw chat
```

## Architecture

SemeClaw is built in 4 phases across 18 progressive steps:

| Phase | Steps | What It Adds |
|-------|-------|-------------|
| **Single Agent** | 00-06 | Chat loop, tools, skills, persistence, commands, compaction, web |
| **Event-Driven** | 07-10 | Event bus, config hot reload, channels, WebSocket API |
| **Multi-Agent** | 11-15 | Routing, cron jobs, layered prompts, messaging, agent dispatch |
| **Production** | 16-17 | Concurrency control, long-term memory |

## Project Structure

```
SemeClaw/
├── src/semeclaw/           # Python source code
│   ├── cli/                # Chat + server CLI commands
│   ├── core/               # Agent, session, events, memory, routing
│   ├── provider/           # LLM, web search, web read providers
│   ├── server/             # Workers, WebSocket, FastAPI, cron
│   ├── tools/              # Built-in + skill + dispatch + web tools
│   ├── channel/            # Telegram, Discord support
│   └── utils/              # Config, definition loader
├── default_workspace/      # Agent definitions & workspace data
│   ├── agents/             # Seme (brain) + Cookie (memory)
│   ├── skills/             # danslab-company, research-workflow
│   ├── memories/           # Long-term knowledge base
│   └── research/           # Structured research workspace
├── pyproject.toml          # Project config & dependencies
└── SemeClaw-Guide.docx     # Complete usage guide
```

## Agents

| Agent | Role | Temperature |
|-------|------|-------------|
| **Seme** | Primary brain — research, coding, ideation, strategy | 0.7 |
| **Cookie** | Memory manager — stores and retrieves knowledge | 0.3 |

## Skills

| Skill | Purpose |
|-------|---------|
| `danslab-company` | Company context, infrastructure, NERVIX details |
| `research-workflow` | Templates for research notes, competitor analysis, architecture decisions |
| `skill-creator` | Instructions for creating new skills |

## Configuration

Edit `default_workspace/config.user.yaml`:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  api_key: your-key-here

# Optional: web search
websearch:
  provider: brave
  api_key: your-brave-api-key

# Optional: web page reading
webread:
  provider: crawl4ai

# Optional: Telegram bot
channels:
  enabled: true
  telegram:
    bot_token: your-bot-token
    allowed_user_ids:
      - "your_user_id"
```

See [PROVIDER_EXAMPLES.md](PROVIDER_EXAMPLES.md) for other LLM providers (OpenAI, Gemini, Grok, etc).

## War Room Dashboard

SemeClaw includes a **War Room** — a cinematic multi-agent meeting dashboard that turns any task report into a voiced agent discussion.

```bash
# Start the War Room dashboard (port 8765)
python war_room/dashboard/server.py
```

Features:
- **Agent Meetings** — Scripted scenarios with distinct neural voices (edge-tts)
- **Cinematic UI** — Boardroom table with animated avatars, real-time transcript, and synced typewriter text
- **Demo Scenarios** — Pre-built meetings for NERVIX positioning, telemetry design, and pricing strategy
- **TTS Caching** — MP3 cache for instant replay

## Deployment

### Docker

```bash
# Build
docker build -t semeclaw:latest .

# Run
docker run -p 8765:8765 -v $(pwd)/default_workspace:/app/default_workspace:ro semeclaw:latest
```

### Docker Compose

```bash
docker-compose up -d
```

### GitHub Container Registry

Images are built and published automatically via GitHub Actions on every release tag:

```bash
docker pull ghcr.io/dansidanutz/semeclaw:latest
```

## Commands

| Command | Description |
|---------|------------|
| `uv run semeclaw chat` | Start interactive chat |
| `uv run semeclaw server` | Start multi-platform server |
| `uv run pytest war_room/tests/` | Run War Room tests |
| `python war_room/dashboard/server.py` | Start War Room dashboard |
| `/help` | List all slash commands |
| `/skills` | Show available skills |
| `/session` | Show session info |
| `/context` | Show token usage |
| `/compact` | Manually compact history |

## Tech Stack

- **Language:** Python 3.10+
- **LLM:** Claude via [litellm](https://docs.litellm.ai/) (supports any provider)
- **CLI:** [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/)
- **API:** [FastAPI](https://fastapi.tiangolo.com/) + WebSocket
- **Config:** YAML with hot reload via [watchdog](https://python-watchdog.readthedocs.io/)
- **Storage:** JSONL for history, Markdown for memory/skills/agents

## Credits

Built following the [build-your-own-openclaw](https://github.com/czl9707/build-your-own-openclaw) tutorial by [@czl9707](https://github.com/czl9707). Customized for [Dan's Lab](https://nervix.ai) and the NERVIX AI agent marketplace.

## License

MIT
