#!/usr/bin/env bash
# Launch the Mac Studio ops goal (scripts/mac-studio-ops-goal.md) in an
# interactive Claude Code session on the Mac Studio.
#
# The stale ANTHROPIC_* overrides broke Claude Max on this machine, so they are
# unset for the launched process only — permanently removing them from shell
# configs is the goal's own first repair, done with backups inside the session.
#
# Usage:  ./scripts/run_mac_studio_goal.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOAL_FILE="$SCRIPT_DIR/mac-studio-ops-goal.md"

if [[ ! -f "$GOAL_FILE" ]]; then
  echo "error: goal file not found: $GOAL_FILE" >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found on PATH. Install Claude Code first:" >&2
  echo "  https://claude.com/claude-code" >&2
  exit 1
fi

# Everything below the first "## The goal prompt" heading is the prompt itself;
# the lines above it are repo documentation and must not be sent to the agent.
PROMPT="$(awk 'found {print} /^## The goal prompt$/ {found=1}' "$GOAL_FILE")"

if [[ -z "$PROMPT" ]]; then
  echo "error: could not extract the goal prompt from $GOAL_FILE" >&2
  exit 1
fi

echo "Starting interactive Claude Code session with the Mac Studio ops goal..."
echo "(ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL / ANTHROPIC_AUTH_TOKEN"
echo " are unset for this session so Claude Max sign-in is used.)"
echo

exec env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_AUTH_TOKEN \
  claude "$PROMPT"
