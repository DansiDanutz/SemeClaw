# SemeClaw improvement goal — release integrity, architecture, hardening

A persistent, resumable goal for any agent (Claude Code, Codex, Cursor) or human
working on SemeClaw. Derived from the 2026-08-26 senior engineering review
(13 findings, SC-01…SC-13). Work through it top to bottom; each phase makes the
next one easier. Update the status column as items land — this file is the
single source of truth for what remains.

**Operating rules**

- Follow `AGENTS.md` and the conventions in `CLAUDE.md`.
- Every change ships with tests where testable and a `CHANGELOG.md` entry under
  `## [Unreleased]` (versions are assigned by the release pipeline, not PRs —
  see `VERSIONING.md`).
- Never point `cloudflare/pages/manifest.json` at unpublished artifacts — the
  manifest advertises only releases whose tag and Docker image already exist.
- Never commit runtime data (`audit/`, `configured-secret/`, `data/`).
- Prefer the smallest reversible change; one concern per PR where possible.

---

## Phase 0 — unblock (owner actions, minutes)

| # | Item | Finding | Status |
|---|------|---------|--------|
| 0.1 | Disable the `daily-release` workflow for one cycle (Actions tab), merge PR #41, re-enable | SC-02, SC-04 | ⬜ owner |
| 0.2 | Decide Greptile: pay, or remove from required checks | SC-08 | ⬜ owner |

## Phase 1 — release integrity (done in PR #41)

| # | Item | Finding | Status |
|---|------|---------|--------|
| 1.1 | Upgrade `cryptography` → 50.0.0, `h2` → 4.4.1 (CVE fixes) | SC-04 | ✅ PR #41 |
| 1.2 | Repair stuck manifest `version` field; pattern-based workflow sed | SC-03 | ✅ PR #41 |
| 1.3 | Order manifest publication after Docker image push | SC-03 | ✅ PR #41 |
| 1.4 | Gate the daily release on lint + full test suite (`test` job) | SC-01 | ✅ PR #41 |
| 1.5 | Skip the daily release when nothing substantive changed since the last tag | SC-02 | ✅ PR #41 |
| 1.6 | Compute the release ZIP's sha256 and publish it in the manifest | SC-03 | ✅ PR #41 |
| 1.7 | Fix boot-crash `logger` use in `server.py` AdClaw fallback (F821) | SC-05/09 | ✅ PR #41 |
| 1.8 | Fix `NameError` broadcast in meeting user-comment route (F821) | SC-05/09 | ✅ PR #41 |
| 1.9 | Re-enable `F821` globally; scope fixture exemption to test dirs | SC-09 | ✅ PR #41 |
| 1.10 | Move `pytest`/`pytest-asyncio` from runtime deps to the dev group | SC-06 | ✅ PR #41 |
| 1.11 | Stop tests hijacking `os.environ.get` globally (CWD data-dir leak) | SC-07 | ✅ PR #41 |
| 1.12 | `.gitignore` backstops for `audit/` and `configured-secret/`; delete `ad-semeclaw.bak.*` | SC-07/10 | ✅ PR #41 |

## Phase 2 — kill the version race (next 2 weeks)

| # | Item | Finding | Status |
|---|------|---------|--------|
| 2.1 | Centralize version assignment in the release pipeline: PRs stop bumping versions; the daily pipeline (test-gated, skip-if-unchanged) mints versions, promotes `[Unreleased]` changelog sections, and refreshes the badge | SC-02 | ✅ PR #41 |
| 2.2 | Delete `scripts/check_version_bumped.sh` PR gate; PR template asks for an `[Unreleased]` changelog entry instead | SC-02 | ✅ PR #41 |
| 2.3 | Release-time smoke test: after deploying, fetch the live manifest from the Pages deployment URL and assert `version` equals the new release and `sha256` is non-empty (this failure mode went unnoticed for months) | SC-03 | ✅ PR #41 |
| 2.4 | Consider cosign signatures for the GHCR image | SC-03 | ⬜ |

## Phase 3 — architecture (next month, incremental)

| # | Item | Finding | Status |
|---|------|---------|--------|
| 3.1 | Extract `APIRouter` modules from `server.py` (6,094 lines / 83 routes) by surface: meetings, voice-agents, embed, tts, admin — one PR each, until `server.py` is app assembly + middleware only. Pattern exists: `routes/advertiser.py`, `routes/assistant.py` | SC-05 | ⬜ |
| 3.2 | Deduplicate module state while extracting (e.g. `SEMECLAW_API_KEY` read twice: lines 77 and 435) into one config module | SC-05 | ⬜ |
| 3.3 | Split `index.html` (5,475 lines) into static JS/CSS files | SC-05 | ⬜ |
| 3.4 | Dependency slimming: extras for `semeclaw[voice]` (kokoro, faster-whisper, onnxruntime, soundfile), `[media]` (yt-dlp), `[aws]` (boto3); port `competitor_dashboard.py` off Flask so `flask`/`flask-cors` drop from the default install; re-run `pip-audit` on the slimmed set | SC-06 | ⬜ |
| 3.5 | Decide the mono-repo question: `apps/` + `packages/` with per-package versioning, or extract `adclaw`/`nervix_platform`/`sentinel`/`coordinator`/`kpis`/`website` — deliberate either way | SC-10 | ⬜ |
| 3.6 | Relocate `Bat/` Windows helpers into `scripts/windows/` (owner sign-off — they may be pinned shortcuts) | SC-10 | ⬜ |

## Phase 4 — hardening for embedders (before the next NERVIX/Paperclip push)

| # | Item | Finding | Status |
|---|------|---------|--------|
| 4.1 | Per-IP rate limits on open read endpoints; stricter on `/api/tts` (unauthenticated ElevenLabs spend) | SC-11 | ⬜ |
| 4.2 | Audit open read endpoints for tenant-scoped data; require tenant tokens where found | SC-11 | ⬜ |
| 4.3 | Enforce minimum JWT signing-secret length (≥32 bytes) at startup, fail closed like `SEMECLAW_API_KEY`; fix short keys in tests (owner sign-off — verify prod key length first so boot doesn't break) | SC-12 | ⬜ |
| 4.4 | `pytest --cov` in CI with a ratcheting floor (start at current, never lower) | SC-13 | ⬜ |
| 4.5 | Error tracking (Sentry or self-hosted GlitchTip) wired into the FastAPI app | SC-13 | ⬜ |
| 4.6 | OpenAPI contract tests asserting `INTEGRATION.md`'s public surface never breaks | SC-11 | ⬜ |
| 4.7 | Ratchet mypy module-by-module, starting with `war_room/v1/` (billing deserves types) | SC-09 | ⬜ |

## Completion criteria

- No release ships untested; no release ships with zero changes.
- The update manifest always advertises the newest published release with a
  verifiable checksum, and CI proves it.
- PRs never race the release bot for version numbers.
- `server.py` under 1,000 lines; default install auditable by an embedder.
- Open endpoints rate-limited; coverage floor and error tracking in place.

**Reference:** full review with evidence → session artifact "SemeClaw Engineering
Review" (2026-08-26); findings SC-01…SC-13 map to the tables above.
