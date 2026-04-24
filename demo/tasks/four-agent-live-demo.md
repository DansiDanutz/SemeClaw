---
id: four-agent-live-demo
title: "Live Demo — Map the open-source AI agent landscape"
status: completed
created: 2026-04-24
duration_estimate: 90s
agents: [browser, scraping, research, writer, coder]
re_runnable: true
---

# Four-Agent Live Demo

A real task that exercises every core agent end-to-end. Run with:

```bash
semeclaw demo
```

## The Task
> "Find the top 3 open-source multi-agent orchestration frameworks on GitHub
> right now. Pull their READMEs, write a 1-page comparison brief covering
> stars / language / license / standout feature, and scaffold a starter
> Python file that imports the top pick."

## Pipeline

| # | Agent     | Action                                                    | Tool                |
|---|-----------|-----------------------------------------------------------|---------------------|
| 1 | Browser   | Search "open source multi-agent orchestration framework github 2026" | `browser_search`    |
| 2 | Browser   | Pick top 3 GitHub repos from results                      | (LLM rank)          |
| 3 | Scraping  | Fetch each repo's README in parallel                      | `scrape_batch`      |
| 4 | Research  | Synthesise: stars / lang / license / standout feature     | (LLM synthesis)     |
| 5 | Writer    | Render comparison brief as markdown                       | (LLM compose)       |
| 6 | Coder     | Generate `starter.py` importing the top pick              | (LLM codegen)       |

## Why This Demo
- Touches every core agent in one run (full surface coverage)
- Uses zero paid services (DuckDuckGo + free OpenRouter models)
- Runs in ~90 seconds on free models
- Output is **real** — the comparison brief and starter scaffold are usable
- Re-runnable: each run picks fresh search results so output evolves

## Saved Outputs
After a successful run, outputs are written to:
- `demo/reports/four-agent-live-demo/<timestamp>/brief.md`
- `demo/reports/four-agent-live-demo/<timestamp>/starter.py`
- `demo/reports/four-agent-live-demo/<timestamp>/run.json` (full transcript)

## Status Tracking
The CLI tracks demo state across runs:
- `pending` — demo never run
- `partial` — some agents missing models / keys
- `completed` — last run succeeded

`semeclaw status` shows current state and which step would block a re-run.
