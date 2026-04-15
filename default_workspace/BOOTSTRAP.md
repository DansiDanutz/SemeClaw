---
title: "SemeClaw Workspace Bootstrap"
description: "Initialization guide for SemeClaw workspace - Dan's Lab"
---

# SemeClaw Workspace - Dan's Lab

This is the workspace for SemeClaw, the AI brain of Dan's Lab Company building NERVIX.

## Directory Structure

- `agents/` - Agent definitions (AGENT.md + SOUL.md)
  - `seme/` - Primary agent (research, ideation, coding)
  - `cookie/` - Memory manager
- `skills/` - Skill definitions (SKILL.md)
  - `danslab-company/` - Company context and infrastructure
  - `research-workflow/` - Research templates and workflows
  - `skill-creator/` - Create new skills
- `crons/` - Scheduled tasks (CRON.md)
- `memories/` - Long-term knowledge storage
  - `topics/` - General knowledge (Dan, infrastructure, etc.)
  - `projects/` - Project-specific info (NERVIX, etc.)
  - `daily-notes/` - Daily logs
- `research/` - Research workspace
  - `notes/` - Daily research notes
  - `competitors/` - Competitor analysis
  - `architecture/` - NERVIX architecture decisions
  - `papers/` - AI paper summaries
  - `market/` - Market research and trends

## Capabilities

### Delegation
Use `subagent_dispatch()` to delegate tasks to specialized agents.

### Memory Operations
Use Cookie agent for memory operations:
```
subagent_dispatch(agent_id="cookie", task="Store this meeting summary", context="...")
subagent_dispatch(agent_id="cookie", task="What do we know about NERVIX architecture?")
```

### Research
Use `danslab-company` skill for company context.
Use `research-workflow` skill for research tasks.
Skills extend your capabilities dynamically.