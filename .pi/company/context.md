# DansLab SemeClaw Repo Context

SemeClaw is the embeddable agent and war-room system for DansLab, NERVIX, and Paperclip companies. This repo combines a standalone agent runtime, a cinematic meeting product surface, and integration paths for external platforms.

## Repo
- Path: /Users/davidai/SemeClaw
- Mission: turn markdown task reports into reviewable, voice-enabled multi-agent meetings and expose that as a productized agent surface
- Owners:
  - Engineering owner: Dan / DansLab
  - Operational owner: DansLab internal agent fleet

## Systems Of Record
- Source control: GitHub repository `DansiDanutz/SemeClaw`
- Product and roadmap docs: `README.md`, `INTEGRATION.md`, `docs/ARCHITECTURE.md`, `SEMECLAW_AGENT_PLAN.md`
- Runtime surfaces:
  - `src/semeclaw/` for the core agent runtime
  - `war_room/` for the monetizable meeting/dashboard surface
  - `default_workspace/` for default agent/workspace behavior
- Communication and downstream consumers:
  - NERVIX marketplace embedding
  - Paperclip company integrations

## Delivery Rules
- Environments:
  - local development commonly runs the dashboard on `:8765`
  - Sentinel runs on `:18790`
  - Coordinator runs on `:8996`
- Release process:
  - keep API/embed contracts aligned with `INTEGRATION.md`
  - preserve the standalone + embeddable product direction described in `SEMECLAW_AGENT_PLAN.md`
- Verification:
  - run focused tests for changed modules
  - verify public endpoint or command behavior for integration-facing changes
  - for agent/provider changes, prefer graceful degradation over hard stops

## Risk Boundaries
- Protected files and directories:
  - `.env` and secrets
  - deployment/service config
  - infra state, logs, and persisted runtime data unless the task explicitly targets them
- Commands requiring human confirmation:
  - production deploys
  - destructive git/history operations
  - database or tenant data changes
  - irreversible cleanups in `war_room/` runtime storage

## Incident Process
- Primary concern: do not let one provider outage or billing issue stop the agent/product loop
- When external providers fail:
  - prefer fallback chains
  - preserve user-visible continuity
  - record the failure mode in docs or memory if it changes operating assumptions
- First status update expectation:
  - identify the failing dependency
  - name the fallback path
  - state whether customer-visible functionality is degraded or preserved

## Notes For Pi
- Read `README.md`, `INTEGRATION.md`, and `SEMECLAW_AGENT_PLAN.md` before making product-level workflow recommendations.
- The repo has both internal brain logic and external product surfaces; keep changes scoped to the right layer.
- Favor resilient behavior. If a provider, adapter, or platform dependency fails, route around it when possible instead of stopping.
