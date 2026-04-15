---
title: "Available Agents"
description: "Guide to agents available in the SemeClaw workspace"
---

# Available Agents

## Seme (default)
The primary AI brain of Dan's Lab. Research companion for NERVIX development, project management, and strategic ideation. Uses skills and web tools actively.

### Capabilities
- Research on AI agent marketplaces and competitors
- Architecture decisions for NERVIX
- Code development and debugging
- Strategic planning and ideation
- Delegates to Cookie for memory operations

### Dispatching to Seme
```
subagent_dispatch(agent_id="seme", task="Research competitor X", context="...")
```

## Cookie
Memory manager for Dan's Lab knowledge. Stores and retrieves information across three axes: topics, projects, and daily-notes.

### Dispatching to Cookie
```
subagent_dispatch(agent_id="cookie", task="Store this meeting summary", context="...")
subagent_dispatch(agent_id="cookie", task="What do we know about NERVIX architecture?")
subagent_dispatch(agent_id="cookie", task="Search for info about 'infrastructure'")
```

## Creating New Agents

Create a directory under `agents/`:
```
agents/your-agent-name/
  AGENT.md          # Required: definition with frontmatter + system prompt
  SOUL.md           # Optional: personality layer
```