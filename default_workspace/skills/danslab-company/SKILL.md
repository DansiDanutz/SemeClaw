---
name: danslab-company
description: Dan's Lab company context, infrastructure, NERVIX product, and team knowledge
trigger: Dan's Lab, NERVIX, company context, infrastructure, architecture
---

# Dan's Lab - Company Context

## Overview
- **Company:** Dan's Lab
- **Main Product:** NERVIX (nervix.ai) - an AI agent marketplace
- **Founder:** Dan (based in Cluj-Napoca, Romania, EET timezone)
- **Telegram Bot:** @DandLabHermes_bot

## Infrastructure
- 4 DigitalOcean droplets: Dexter, Memo, Sienna, Nano
- Main orchestrator: David (OpenClaw-based)
- Pilot/canary agent: Hermes (@DandLabHermes_bot)
- Mac Studio server in Cluj-Napoca (local backend)

## Technology Stack
- **Hermes Agent:** Python-based AI agent framework with CLI + multi-platform gateway
- **Main Model:** qwen3.6-plus via Alibaba DashScope
- **Fallback Models:** GPT-5.4 (OpenAI), Gemini 2.5 Flash (Google)
- **Database:** SQLite with FTS5 for session search
- **Skills System:** Markdown-based skill definitions

## Key Concepts
- NERVIX is being built as a marketplace where users can discover, buy, and deploy AI agents
- Dan's Lab runs a fleet of AI agents managed by the "David" orchestrator
- Hermes is the pilot agent testing the agent framework
- The system supports: Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant

## Research Focus Areas
- AI agent marketplace design and monetization
- Multi-agent orchestration patterns
- LLM routing and cost optimization
- Open-source AI infrastructure
- Agent evaluation and benchmarking
- MCP (Model Context Protocol) ecosystem
- Nous Research models (Hermes family) as potential NERVIX offerings

## Conventions
- Direct, concise communication - no fluff
- Be proactive and honest about capabilities
- Python with uv for dependency management