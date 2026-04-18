# SemeClaw Demo

Run the demo in 60 seconds:

```bash
git clone https://github.com/Dansidanutz/SemeClaw.git
cd SemeClaw
./setup.sh
DEMO_MODE=true uv run python war_room/dashboard/server.py
# → http://127.0.0.1:8765
```

The demo loads 4 pre-built agents (Aria, Bolt, Luna, Echo), 3 sample tasks, and a
pre-built report you can open in the Meeting Room immediately — no paid API keys needed.

## Demo Agents

| Agent | Role | Personality |
|-------|------|-------------|
| **Aria** | Strategic Orchestrator | Calm, big-picture thinker. Opens and closes every meeting. |
| **Bolt** | Senior Developer | Blunt, fast, technical. Ships things. |
| **Luna** | Product Manager | Organized, asks the right questions, catches gaps. |
| **Echo** | Research Analyst | Data-driven, brings references, questions assumptions. |

## Demo Tasks (pre-loaded)

1. **Build a SaaS landing page** — full pipeline with research + strategy + build
2. **Competitor analysis** — deep research + strategic brief
3. **API integration sprint** — technical planning + task breakdown

## Ollama (free local LLM)

The demo uses Ollama with `qwen3:8b` by default — free, private, runs on your machine.

```bash
# Install Ollama (macOS)
brew install ollama
ollama pull qwen3:8b
```

Or set `OPENROUTER_API_KEY` for cloud models.
