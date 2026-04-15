# Dan's Lab Infrastructure

## Cloud
- 4 DigitalOcean droplets: Dexter, Memo, Sienna, Nano
- Main orchestrator: David (OpenClaw-based)
- Pilot/canary agent: Hermes (@DandLabHermes_bot)

## Local
- Mac Studio server in Cluj-Napoca (local backend)

## Models
- Main: qwen3.6-plus via Alibaba DashScope
- Fallback: GPT-5.4 (OpenAI), Gemini 2.5 Flash (Google)
- Database: SQLite with FTS5 for session search