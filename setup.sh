#!/usr/bin/env bash
# =============================================================================
# SemeClaw — one-command installer
# Usage: chmod +x setup.sh && ./setup.sh
# =============================================================================
set -e

SEMECLAW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()    { echo -e "${BOLD}[setup]${RESET} $*"; }
success() { echo -e "${GREEN}[setup]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[setup]${RESET} $*"; }
error()   { echo -e "${RED}[setup] ERROR:${RESET} $*" >&2; }

# ── 1. Python version check ───────────────────────────────────────────────────
info "Checking Python version..."

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    error "Python 3.10+ is required but not found."
    error "Install from https://python.org or via your package manager."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    error "Python 3.10+ required, found Python $PYTHON_VERSION via $PYTHON_BIN."
    exit 1
fi

success "Python $PYTHON_VERSION found at $(command -v $PYTHON_BIN)"

# ── 2. Install uv if missing ──────────────────────────────────────────────────
info "Checking for uv..."

if ! command -v uv &>/dev/null; then
    warn "uv not found. Installing via astral.sh..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Reload PATH so the newly installed uv is visible
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        error "uv installation succeeded but 'uv' is still not on PATH."
        error "Add ~/.local/bin to your PATH, then re-run this script."
        exit 1
    fi
    success "uv installed: $(uv --version)"
else
    success "uv found: $(uv --version)"
fi

# ── 3. Install Python dependencies ────────────────────────────────────────────
info "Running 'uv sync' to install dependencies..."
cd "$SEMECLAW_DIR"
uv sync
success "Dependencies installed."

# ── 4. Copy .env.example → .env (never overwrite) ─────────────────────────────
info "Checking .env..."
if [ -f "$SEMECLAW_DIR/.env" ]; then
    warn ".env already exists — skipping copy to avoid overwriting your settings."
else
    cp "$SEMECLAW_DIR/.env.example" "$SEMECLAW_DIR/.env"
    success ".env created from .env.example"
fi

# ── 5. Print env var checklist ─────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Required environment variables — fill these in .env:${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${YELLOW}ELEVENLABS_API_KEY${RESET}          — ElevenLabs Flash v2.5 voice engine"
echo -e "                                 https://elevenlabs.io/api"
echo ""
echo -e "  ${YELLOW}OPENROUTER_API_KEY${RESET}          — LLM routing (redirect/replan/finalize)"
echo -e "                                 https://openrouter.ai/keys"
echo ""
echo -e "  ${YELLOW}SEMECLAW_API_KEY${RESET}            — Bearer token for write endpoints"
echo -e "                                 (any strong random string; leave unset for open dev mode)"
echo ""
echo -e "  ${YELLOW}DLS_TEAM_SUPABASE_URL${RESET}       — Moltbot/DansLab Supabase project URL"
echo -e "                                 https://supabase.com/dashboard → project settings"
echo ""
echo -e "  ${YELLOW}DLS_TEAM_SUPABASE_SERVICE_KEY${RESET} — Supabase service_role secret key"
echo -e "                                 (same dashboard → API → service_role)"
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""

# ── 6. macOS LaunchAgent install (optional) ────────────────────────────────────
if [ "$(uname -s)" = "Darwin" ]; then
    echo -e "${BOLD}macOS detected.${RESET} Install LaunchAgents for auto-start on login?"
    echo -e "  This will register ${BOLD}Sentinel${RESET} (:18790) and ${BOLD}Coordinator${RESET} (:8996) as background services."
    echo -n "  Install LaunchAgents? [y/N] "
    read -r INSTALL_AGENTS

    if [ "$INSTALL_AGENTS" = "y" ] || [ "$INSTALL_AGENTS" = "Y" ]; then
        LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
        mkdir -p "$LAUNCH_AGENTS_DIR"

        PLIST_SRC="$SEMECLAW_DIR/systemd"

        for PLIST_NAME in com.danslab.sentinel.plist com.danslab.coordinator.plist; do
            SRC="$PLIST_SRC/$PLIST_NAME"
            DEST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

            if [ ! -f "$SRC" ]; then
                warn "Plist not found: $SRC — skipping $PLIST_NAME"
                continue
            fi

            # Replace SEMECLAW_DIR placeholder with real path
            sed "s|SEMECLAW_DIR|$SEMECLAW_DIR|g" "$SRC" > "$DEST"
            success "Installed $PLIST_NAME → $DEST"

            # Load the LaunchAgent (unload first to avoid duplicate warnings)
            launchctl unload "$DEST" 2>/dev/null || true
            launchctl load "$DEST"
            success "Loaded $PLIST_NAME"
        done
    else
        info "Skipping LaunchAgent install. You can run services manually (see README)."
    fi
fi

# ── 7. Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✅ SemeClaw ready.${RESET}"
echo ""
echo -e "  Start the War Room dashboard:"
echo -e "  ${BOLD}uv run python war_room/dashboard/server.py${RESET}"
echo ""
echo -e "  Start Sentinel (fleet health monitor):"
echo -e "  ${BOLD}uv run python -m sentinel.sentinel${RESET}"
echo ""
echo -e "  Start Coordinator (LLM circuit-breaker proxy):"
echo -e "  ${BOLD}uv run python -m coordinator.coordinator${RESET}"
echo ""
echo -e "  Dashboard → ${BOLD}http://127.0.0.1:8765${RESET}"
echo ""
echo ""
echo -e "══════════════════════════════════════════════"
echo -e "  🎭 DEMO MODE (no API keys needed)"
echo -e "══════════════════════════════════════════════"
echo -e "  Install Ollama + qwen3:8b for free local LLM:"
echo -e "    brew install ollama && ollama pull qwen3:8b"
echo ""
echo -e "  Then run in demo mode:"
echo -e "    DEMO_MODE=true uv run python war_room/dashboard/server.py"
echo -e "══════════════════════════════════════════════"
echo ""
