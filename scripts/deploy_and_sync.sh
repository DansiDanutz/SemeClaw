#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# SemeClaw — single-source deploy + fleet sync
# ─────────────────────────────────────────────────────────────────────────
# What it does, in order:
#   1. Compute a build version: <pyproject_version>+<git_short_sha>.<utc_ts>
#   2. Write version manifest to ad-semeclaw/version.json AND
#      war_room/dashboard/static/version.json so /version returns it on each
#      deployed surface.
#   3. Commit + push (skipped if --no-commit).
#   4. Deploy ad-semeclaw to Vercel (skipped if --no-vercel).
#   5. Deploy war_room to Fly (skipped if --no-fly).
#   6. SSH to each Tailscale node and run `git -C ~/SemeClaw pull --rebase`
#      so every machine in the fleet is on the same commit (skipped if
#      --no-fleet). Failures don't abort the run — they're reported at end.
#   7. Print a final report: deployed version, URLs, fleet sync results.
#
# Usage:
#   bash scripts/deploy_and_sync.sh                    # full run
#   bash scripts/deploy_and_sync.sh --no-fleet         # skip Tailscale sync
#   bash scripts/deploy_and_sync.sh --no-commit        # build + deploy current HEAD
#   FLEET_HOSTS="memo dexter" bash scripts/deploy_and_sync.sh
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DO_COMMIT=1; DO_VERCEL=1; DO_FLY=1; DO_FLEET=1
for a in "$@"; do
  case "$a" in
    --no-commit) DO_COMMIT=0 ;;
    --no-vercel) DO_VERCEL=0 ;;
    --no-fly)    DO_FLY=0 ;;
    --no-fleet)  DO_FLEET=0 ;;
    *) echo "unknown flag: $a"; exit 2 ;;
  esac
done

# Tailscale fleet — override with FLEET_HOSTS env var.
FLEET_HOSTS="${FLEET_HOSTS:-memo dexter sienna nano mac-mini mac-studio}"

# 1. Compute version --------------------------------------------------------
PYPROJECT_VERSION=$(grep -E '^version\s*=' pyproject.toml 2>/dev/null \
  | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
[ -z "$PYPROJECT_VERSION" ] && PYPROJECT_VERSION="0.0.0"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
DIRTY=$(git diff --quiet 2>/dev/null && echo "" || echo "+dirty")
BUILD_TS=$(date -u +%Y%m%dT%H%M%SZ)
BUILD_VERSION="${PYPROJECT_VERSION}+${GIT_SHA}${DIRTY}.${BUILD_TS}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Build version: $BUILD_VERSION"
echo "  Branch:        $GIT_BRANCH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 2. Write version manifests ------------------------------------------------
write_version() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<EOF
{
  "version": "$BUILD_VERSION",
  "pyproject_version": "$PYPROJECT_VERSION",
  "git_sha": "$GIT_SHA",
  "git_branch": "$GIT_BRANCH",
  "built_at": "$BUILD_TS",
  "dirty": $([ -n "$DIRTY" ] && echo true || echo false)
}
EOF
}
write_version "ad-semeclaw/version.json"
write_version "war_room/dashboard/version.json"

# 3. Commit + push ----------------------------------------------------------
if [ "$DO_COMMIT" -eq 1 ]; then
  if git diff --quiet && git diff --cached --quiet; then
    echo "› no uncommitted changes — skipping commit"
  else
    git add -A
    git commit -m "build: $BUILD_VERSION" >/dev/null
    echo "› committed"
  fi
  git push origin "$GIT_BRANCH" 2>&1 | tail -2
fi

# 4. Vercel ad-semeclaw -----------------------------------------------------
VERCEL_URL=""
if [ "$DO_VERCEL" -eq 1 ]; then
  echo "› deploying ad-semeclaw to Vercel..."
  ( cd ad-semeclaw && vercel deploy --prod --yes 2>&1 | tail -5 )
  VERCEL_URL="https://ad-semeclaw.vercel.app"
fi

# 5. Fly war_room ----------------------------------------------------------
if [ "$DO_FLY" -eq 1 ]; then
  echo "› deploying war_room to Fly..."
  fly deploy -a semeclaw --remote-only 2>&1 | tail -5 || echo "  (fly deploy returned non-zero — check logs)"
fi

# 6. Tailscale fleet --------------------------------------------------------
declare -A FLEET_STATUS
if [ "$DO_FLEET" -eq 1 ]; then
  echo "› syncing Tailscale fleet: $FLEET_HOSTS"
  for host in $FLEET_HOSTS; do
    out=$(ssh -o ConnectTimeout=8 -o BatchMode=yes "$host" \
      "cd ~/SemeClaw 2>/dev/null && git fetch --quiet && git rev-parse --abbrev-ref HEAD && git pull --rebase --autostash 2>&1 | tail -1" 2>&1) || true
    if echo "$out" | grep -qiE "fatal|error|denied|refused|closed"; then
      FLEET_STATUS[$host]="✗ $(echo "$out" | head -1 | tr -d '\n' | cut -c1-80)"
    else
      FLEET_STATUS[$host]="✓ $(echo "$out" | tail -1 | tr -d '\n' | cut -c1-80)"
    fi
  done
fi

# 7. Final report -----------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DEPLOY REPORT — $BUILD_VERSION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ -n "$VERCEL_URL" ] && echo "  ad-semeclaw: $VERCEL_URL/version.json"
[ "$DO_FLY"    -eq 1 ] && echo "  semeclaw  : https://semeclaw.fly.dev/version.json"
if [ "$DO_FLEET" -eq 1 ]; then
  echo "  Fleet:"
  for host in $FLEET_HOSTS; do
    printf "    %-12s %s\n" "$host" "${FLEET_STATUS[$host]:-(skipped)}"
  done
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
