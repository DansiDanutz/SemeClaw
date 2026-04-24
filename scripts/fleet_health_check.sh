#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FLEET_HOSTS="${FLEET_HOSTS:-memo dexter sienna nano mac-mini mac-studio}"
REPORT_DIR="${REPORT_DIR:-$ROOT/logs}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_PATH="$REPORT_DIR/fleet-health-$STAMP.txt"

mkdir -p "$REPORT_DIR"

emit() {
  printf '%s\n' "$1" | tee -a "$REPORT_PATH"
}

section() {
  emit ""
  emit "## $1"
}

check_http() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
    emit "OK   $name $url"
  else
    emit "FAIL $name $url"
  fi
}

emit "# Fleet Health Report"
emit "Generated: $STAMP"
emit "Hosts: $FLEET_HOSTS"

section "Local Services"
check_http "war-room"   "http://127.0.0.1:8765/health"
check_http "sentinel"   "http://127.0.0.1:18790/health"
check_http "coordinator" "http://127.0.0.1:8996/health"

section "Tailscale"
if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    emit "OK   tailscale status"
  else
    emit "FAIL tailscale status"
  fi

  for host in $FLEET_HOSTS; do
    if tailscale ping --timeout=5s "$host" >/dev/null 2>&1; then
      emit "OK   tailscale ping $host"
    else
      emit "FAIL tailscale ping $host"
    fi
  done
else
  emit "FAIL tailscale CLI not installed"
fi

section "SSH Reachability"
for host in $FLEET_HOSTS; do
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" "echo ok" >/dev/null 2>&1; then
    emit "OK   ssh $host"
  else
    emit "FAIL ssh $host"
  fi
done

section "Remote Health"
for host in dexter memo sienna nano; do
  out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" "curl -fsS --max-time 5 http://127.0.0.1:18789/health" 2>/dev/null || true)"
  if [ -n "$out" ]; then
    emit "OK   $host gateway health"
  else
    emit "FAIL $host gateway health"
  fi
done

section "Mac Studio"
if ssh -o BatchMode=yes -o ConnectTimeout=8 mac-studio "curl -fsS --max-time 5 http://127.0.0.1:8765/version && echo && curl -fsS --max-time 5 http://127.0.0.1:18790/status && echo && curl -fsS --max-time 5 http://127.0.0.1:8996/health" >/dev/null 2>&1; then
  emit "OK   mac-studio local service bundle"
else
  emit "FAIL mac-studio local service bundle"
fi

emit ""
emit "Report saved to $REPORT_PATH"
