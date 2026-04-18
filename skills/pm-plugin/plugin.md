---
name: semeclaw-war-room
description: Convene, audit, and interact with SemeClaw War Room meetings. Use this plugin when working on NERVIX strategy, architecture decisions, or any task that benefits from multi-agent synthesis with human participation.
version: "1.0.0"
author: Dan's Lab
commands:
  - convene
  - audit-agents
  - join-meeting
  - inject
skills:
  - convene-meeting
  - audit-agents
  - join-meeting
  - inject-requirement
  - skill-registry
---

# SemeClaw War Room Plugin

This plugin integrates SemeClaw's War Room meeting system into your PM workflow.

## When to Use

- You need multi-agent synthesis on a complex topic (use `/semeclaw:convene`)
- You want to understand who is in a meeting and what they know (use `/semeclaw:audit-agents`)
- You want to join a live meeting and ask questions (use `/semeclaw:join-meeting`)
- You have a new requirement mid-meeting (use `/semeclaw:inject`)

## Plugin Commands

### `/semeclaw:convene <subject>`
Convenes a War Room meeting on the given subject. Triggers Autoresearch → GSD → Hermes → David pipeline.

### `/semeclaw:audit-agents <meeting-name>`
Returns skill cards for every agent in the meeting — what they know, how to talk to them, who they hand off to.

### `/semeclaw:join-meeting <meeting-name>`
Returns the meeting player URL and the human interaction guide for joining a live meeting.

### `/semeclaw:inject "<message>" [agent]`
Injects a question or requirement into a running meeting. Optional: specify which agent to direct it to.

## API Quick Reference

```
GET  /api/agents/skills              — all agent skill cards
GET  /api/agents/skills/{id}         — full skill card for one agent
GET  /api/meeting/agents?name={name} — agents in a specific meeting
POST /api/meeting/inject             — human injects requirement/question
GET  /api/agent/manifest             — full capability manifest
```
