# SemeClaw audit

Date: 2026-04-19
Auditor: Hermes

## Summary

SemeClaw is a strong product-shaped codebase with a clear differentiator: the War Room meeting surface and embed/API integration path. The main issues found were operational rather than conceptual: broken pytest collection, auth/test mismatch, internal version drift, and runtime artifacts tracked in git.

## What was audited

- Repository layout and packaging
- Public API surface in `war_room/dashboard/server.py`
- Test suite health
- Runtime artifact hygiene
- Version consistency
- A few security-sensitive implementation details

## Main findings

### Strengths

- Clear product surface and monetization path
- Good split between `src/semeclaw` core logic and `war_room` public surface
- Meaningful test coverage
- Auth, CORS, and CSP support already present
- Deployment artifacts exist (`pyproject.toml`, `Dockerfile`, integration docs)

### Issues found

1. `pytest` collection failed because `war_room/tests/conftest.py` declared `pytest_plugins` at a non-top-level location.
2. `tests/test_api.py` fixtures for open vs authenticated clients were out of sync with current auth behavior.
3. `server.py` exposed multiple stale `0.6.0` values while the project had already moved to `0.7.0`.
4. Several runtime artifacts and generated files were still tracked by git despite ignore rules.
5. A few env-var lines in `server.py` needed cleanup during the version/auth pass.

## Changes applied

### Test collection

- Added repo-root `conftest.py` with `pytest_asyncio` plugin registration.
- Simplified `war_room/tests/conftest.py` so pytest 8+ no longer aborts collection.

### Auth/test mismatch

- Updated `tests/test_api.py` fixtures so:
  - `open_client` runs with no API key enforced
  - `auth_client` runs with `_TEST_API_KEY`
  - fixtures restore original state after each use
- Updated version assertions to use `srv.APP_VERSION`

### Version consistency

- Introduced `APP_VERSION = "0.7.0"` in `war_room/dashboard/server.py`
- Reused it for:
  - FastAPI app version
  - `X-SemeClaw-Version` header
  - event/webhook payload version fields
  - transcript footer helper
  - Prometheus metrics version label
- Updated Docker comments from `0.6.0` to `0.7.0`

### Runtime hygiene

Removed the following generated/runtime files from git index with `git rm --cached` while preserving local copies:

- `war_room/audio/_test.mp3`
- `war_room/audio/meetings/saved/75f6809d_quick-test_-what-is-nervix_-2026-04-17.mp3`
- `war_room/audio/scripts/75f6809d_ro.json`
- `war_room/builds/danslab-company-website-2026-04-18/index.html`
- `war_room/memory/memory.json`
- `war_room/research/[moltbot]-nervix:-npm-publish-nervix-cli-2026-04-18.md`
- `war_room/research/pc-pc-9999.md`
- `war_room/shared_state.json`

## Verification

### Targeted verification

Ran:

- `python3 -m py_compile war_room/dashboard/server.py tests/test_api.py conftest.py war_room/tests/conftest.py`
- `uv run pytest -q tests/test_api.py::TestAuth::test_pin_without_key_401 tests/test_api.py::TestReports::test_create_report_201 tests/test_api.py::TestHealth::test_version_header_present tests/test_api.py::TestMetrics::test_metrics_has_version_label`

Result:

- Compile check passed
- Targeted tests passed

## Remaining recommendations

1. Migrate from `@app.on_event("startup")` to FastAPI lifespan handlers.
2. Review the `builtin_tools.bash()` tool because `shell=True` is a real security footgun if this surface ever becomes less trusted.
3. Consider centralizing package version metadata further so docs and runtime derive from the same source.
4. Add CI checks to reject tracked runtime files and to run full pytest on every push.

## Verdict

SemeClaw remains a strong prototype with a credible external product surface. After the changes above it is in a better state for continued development, but it would still benefit from one more pass on lifecycle hooks, CI hardening, and local-shell security boundaries.
