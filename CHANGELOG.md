# Changelog

All notable changes to SemeClaw will be documented in this file.

## [0.7.0] - 2026-04-20

### Added
- 6 external platform adapters: Paperclip, Multica, GitHub, Obsidian, Ollama, Telegram
- Telegram bot integration with command handlers (/run, /status, /board, /link, /help)
- Onboarding module: discovery, seed, sync
- Agent run history and health tracking
- Live comment injection during meetings
- Task-driven meeting system with LLM script generation
- Neural TTS via edge-tts (20+ unique voices)
- ElevenLabs Flash v2.5 premium voice layer
- Docker Compose support
- Fly.io deployment configuration

### Changed
- Migrated from PaperclipBridge to PaperclipAdapter
- Refactored dashboard API endpoints
- Improved meeting template system

### Fixed
- Auth guard improvements
- TTS pipeline reliability
- Startup deduplication
- AdClaw integration stability

## [0.6.0] - 2026-04-15

### Added
- Voice cloning and transcript generation
- Slack integration
- GitHub Action workflow
- Stripe billing scaffold
- pytest suite

## [0.5.0] - 2026-04-10

### Added
- Voice overrides
- Meeting templates
- Cost ledger
- Fly.io deployment support

## [0.4.0] - 2026-04-05

### Added
- Server-Sent Events (SSE)
- NERVIX card integration
- Paperclip adapter
- Theater mode

## [0.3.0] - 2026-03-28

### Added
- Data ingestion pipeline
- Multi-tenant support
- Webhooks
- Metrics endpoint
- Share links
- CI/CD pipeline

## [0.2.0] - 2026-03-20

### Added
- Professional README
- Architecture documentation
- API reference
- Embeddable agent interface

## [0.1.0] - 2026-03-15

### Added
- Initial SemeClaw release
- War Room dashboard
- Basic agent orchestration
- Paperclip bridge
