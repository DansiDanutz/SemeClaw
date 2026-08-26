# Versioning policy

SemeClaw follows [Semantic Versioning 2.0](https://semver.org/).

The canonical version lives in `pyproject.toml`:

```toml
version = "X.Y.Z"
```

and is propagated to `war_room/dashboard/server.py` (`APP_VERSION`) and to
HTTP responses via the `X-SemeClaw-Version` header and the
`GET /api/agent/manifest` payload.

## Who assigns versions

**The release pipeline, not pull requests.** Versions are minted exclusively
by the daily-release workflow (`.github/workflows/daily-release.yml`), which:

1. runs the full lint + test suite (nothing untested is ever tagged),
2. **skips the release entirely** when nothing substantive changed since the
   last tag — no more identical-code releases,
3. bumps the patch version in `pyproject.toml` (skipping already-used tags),
4. promotes any `## [Unreleased]` section in `CHANGELOG.md` to the new
   version, refreshes the README badge, commits, tags, and pushes,
5. hashes the exact release ZIP and publishes the update manifest with its
   `sha256`, then verifies the *deployed* manifest advertises the new
   version with a checksum.

PRs therefore **do not bump versions** and can never race the release bot
for version numbers. (The old `check_version_bumped.sh` PR gate is gone —
its interaction with the daily bot forced open PRs into daily manual
version leapfrogs; see PR #41's history for the case study.)

## What a PR does instead

Add a changelog entry under a top-level `## [Unreleased]` heading using
keep-a-changelog sections (`### Added`, `### Changed`, `### Fixed`,
`### Removed`, `### Security`). The next release run stamps it with the
version and date automatically.

MINOR/MAJOR bumps (new surface, breaking change) are deliberate acts: set
the base version in `pyproject.toml` in the PR that introduces the change
(e.g. `0.11.0`), and the pipeline continues patching from there.

## Release flow

1. Merge PRs into `main` freely — no version choreography.
2. At 06:00 UTC (or on `workflow_dispatch`) the daily-release pipeline
   tests, decides whether anything shipped-worthy changed, and if so
   tags/builds/deploys and publishes a checksummed manifest.
3. `fly.toml` pins no version — the latest GHCR tag is what lands. Users
   can point at a specific tag via `image = "ghcr.io/dansidanutz/semeclaw:X.Y.Z"`.

## Pre-release versions

For experimental work land under a pre-release suffix:

```
version = "0.8.0rc1"
```

Pre-release suffixes (`rc1`, `beta2`, `dev0`, etc.) are not released by the
tag workflow. Remove the suffix before tagging.

## Why

Every user — internal operator, API consumer, embedded iframe host —
should be able to point at `X-SemeClaw-Version` or `/api/agent/manifest`
and know exactly what code is running, and verify what they downloaded
matches what was released. Centralizing version assignment in one tested
pipeline keeps that guarantee without taxing every PR.
