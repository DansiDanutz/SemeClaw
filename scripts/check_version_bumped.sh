#!/usr/bin/env bash
# CI: fail if pyproject.toml version equals main's version and any non-docs
# file was touched. Meant to run on pull_request events.
#
# Usage: bash scripts/check_version_bumped.sh [base-ref]
#        Default base-ref: origin/main
set -euo pipefail

BASE="${1:-origin/main}"

version_at() {
    git show "$1:pyproject.toml" | sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

HEAD_VER="$(version_at HEAD)"
BASE_VER="$(version_at "$BASE")"

if [[ -z "$HEAD_VER" || -z "$BASE_VER" ]]; then
    echo "::error::Could not read version from pyproject.toml (HEAD=$HEAD_VER, $BASE=$BASE_VER)"
    exit 1
fi

if [[ "$HEAD_VER" != "$BASE_VER" ]]; then
    echo "OK — version bumped: $BASE_VER -> $HEAD_VER"
    exit 0
fi

# Versions match — only pass if all touched files are doc-only.
DOC_PATTERN='^(docs/|README|AUDIT|CHANGELOG|CONTRIBUTING|LICENSE|VERSIONING|INTEGRATION|PLAN|PRODUCTION|STATUS|OPENCLAW|SEMECLAW_AGENT|AGENTS|CLAUDE)'
NON_DOC=$(git diff --name-only "$BASE"...HEAD | grep -vE "$DOC_PATTERN" | grep -v '\.md$' || true)

if [[ -z "$NON_DOC" ]]; then
    echo "OK — version unchanged but PR is docs-only (allowed)."
    exit 0
fi

echo "::error::Version in pyproject.toml was not bumped ($BASE_VER -> $HEAD_VER, unchanged)."
echo "         The PR modifies non-doc files:"
echo "$NON_DOC" | sed 's/^/         - /'
echo ""
echo "Run one of:"
echo "    python scripts/bump_version.py patch     # bug fix / perf / hygiene"
echo "    python scripts/bump_version.py minor     # new feature / new endpoint"
echo "    python scripts/bump_version.py major     # breaking change"
echo "Then append a '## [X.Y.Z] - YYYY-MM-DD' section to CHANGELOG.md."
exit 1
