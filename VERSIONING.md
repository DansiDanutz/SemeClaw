# Versioning policy

SemeClaw follows [Semantic Versioning 2.0](https://semver.org/). Every change
to the codebase bumps the version.

The canonical version lives in `pyproject.toml`:

```toml
version = "X.Y.Z"
```

and is propagated to `war_room/dashboard/server.py` (`APP_VERSION`) and to
HTTP responses via the `X-SemeClaw-Version` header and the
`GET /api/agent/manifest` payload.

## Bumping rules

| Bump | When |
|---|---|
| **PATCH** (0.7.14 → 0.7.15) | Bug fix, hygiene, perf improvement, docs, tests |
| **MINOR** (0.7.15 → 0.8.0) | New feature, new endpoint, new config knob, behaviour change that's backwards-compat |
| **MAJOR** (0.7.15 → 1.0.0) | Breaking API change, removed endpoint, changed required env var name, changed DB schema in a non-additive way |

**Every PR must bump the version.** CI enforces this via
`scripts/check_version_bumped.sh`, which compares the `pyproject.toml`
version on the PR branch to the one on `main`. If the version is the same,
CI fails.

## How to bump

Use the existing helper:

```bash
python scripts/bump_version.py patch    # 0.7.15 -> 0.7.16
python scripts/bump_version.py minor    # 0.7.15 -> 0.8.0
python scripts/bump_version.py major    # 0.7.15 -> 1.0.0
python scripts/bump_version.py 0.9.3    # set explicitly
```

Then append a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md` describing
what changed. Keep a short bulleted list under `### Added`, `### Changed`,
`### Fixed`, `### Removed` headings (keep-a-changelog conventions).

## Release flow

1. Merge a version-bumped PR into `main`.
2. The daily-release workflow (`.github/workflows/daily-release.yml`) or a
   manual `git tag vX.Y.Z && git push --tags` kicks the release workflow
   (`ci.yml`), which builds & pushes the image to GHCR and runs
   `flyctl deploy`.
3. `fly.toml` pins no version — the latest GHCR tag is what lands. Users
   can point at a specific tag via `image = "ghcr.io/dansidanutz/semeclaw:X.Y.Z"`.

## Pre-release versions

For experimental work land under a pre-release suffix:

```
version = "0.8.0rc1"
```

Pre-release suffixes (`rc1`, `beta2`, `dev0`, etc.) are not released by the
tag workflow. Remove the suffix before tagging.

## What counts as "didn't bump"?

CI fails the version-bump check if:

- `pyproject.toml` version on the PR is **lexicographically equal** to the
  version on `main`, AND
- the PR touches any file under `src/`, `war_room/`, `adclaw/`,
  `nervix_platform/`, `scripts/`, `integrations/`, or the project root
  `*.py`, `*.toml`, `*.sh`.

Pure documentation PRs (README/CHANGELOG/docs/*.md only) skip the check —
CI detects them and passes the step.

## Why

Every user — internal operator, API consumer, embedded iframe host —
should be able to point at `X-SemeClaw-Version` or `/api/agent/manifest`
and know exactly what code is running. Without a bump per PR, "latest"
becomes meaningless and `fly deploy` surprises happen.
