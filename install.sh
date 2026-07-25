#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# SemeClaw — One-Line Installer
# curl -fsSL https://raw.githubusercontent.com/DansiDanutz/SemeClaw/main/install.sh | bash
# ═══════════════════════════════════════════════════════════════════════

set -e

REPO_URL="https://github.com/DansiDanutz/SemeClaw.git"
INSTALL_DIR="${HOME}/SemeClaw"
PYTHON_MIN="3.10"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${CYAN}ℹ $1${NC}"; }
ok()    { echo -e "${GREEN}✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
error() { echo -e "${RED}✗ $1${NC}"; }

print_banner() {
    echo ""
    echo -e "${CYAN}  ███████╗███████╗███╗   ███╗███████╗ ██████╗██╗      █████╗ ██╗    ██╗${NC}"
    echo -e "${CYAN}  ██╔════╝██╔════╝████╗ ████║██╔════╝██╔════╝██║     ██╔══██╗██║    ██║${NC}"
    echo -e "${CYAN}  ███████╗█████╗  ██╔████╔██║█████╗  ██║     ██║     ███████║██║ █╗ ██║${NC}"
    echo -e "${CYAN}  ╚════██║██╔══╝  ██║╚██╔╝██║██╔══╝  ██║     ██║     ██╔══██║██║███╗██║${NC}"
    echo -e "${CYAN}  ███████║███████╗██║ ╚═╝ ██║███████╗╚██████╗███████╗██║  ██║╚███╔███╔╝${NC}"
    echo -e "${CYAN}  ╚══════╝╚══════╝╚═╝     ╚═╝╚══════╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ${NC}"
    echo -e "${CYAN}                    Agent-Powered War Room v0.7.0${NC}"
    echo ""
}

check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        if awk -v v="$PYTHON_VERSION" -v min="$PYTHON_MIN" 'BEGIN { exit !(v >= min) }'; then
            ok "Python $PYTHON_VERSION found"
            return 0
        fi
    fi
    if command -v python &> /dev/null; then
        PYTHON_VERSION=$(python -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        if awk -v v="$PYTHON_VERSION" -v min="$PYTHON_MIN" 'BEGIN { exit !(v >= min) }'; then
            ok "Python $PYTHON_VERSION found"
            return 0
        fi
    fi
    error "Python $PYTHON_MIN+ is required but not found."
    info "Install Python: https://www.python.org/downloads/"
    exit 1
}

check_uv() {
    if command -v uv &> /dev/null; then
        ok "uv found: $(uv --version)"
        return 0
    fi
    info "Installing uv (Python package manager)..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        ok "uv installed"
    else
        error "Failed to install uv"
        exit 1
    fi
}

clone_repo() {
    if [ -d "$INSTALL_DIR" ]; then
        warn "SemeClaw already exists at $INSTALL_DIR"
        read -p "Update existing installation? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            info "Updating existing installation..."
            cd "$INSTALL_DIR"
            git pull --ff-only
        else
            info "Using existing installation."
            cd "$INSTALL_DIR"
        fi
    else
        info "Cloning SemeClaw into $INSTALL_DIR..."
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
        ok "Cloned successfully"
    fi
}

install_deps() {
    info "Installing dependencies with uv..."
    uv sync --dev
    ok "Dependencies installed"
}

setup_env() {
    if [ ! -f .env ]; then
        info "Creating .env from template..."
        cp .env.example .env
        ok ".env created — edit it to add your API keys"
    else
        ok ".env already exists"
    fi
}

setup_workspace() {
    if [ ! -d default_workspace ]; then
        info "Creating default workspace..."
        mkdir -p default_workspace/agents/assistant
        mkdir -p default_workspace/skills
        mkdir -p default_workspace/crons
        mkdir -p default_workspace/memories
        ok "Workspace created"
    fi
}

print_next_steps() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  SemeClaw is installed and ready!                                    ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}Next: 60-second onboarding${NC}"
    echo "  cd $INSTALL_DIR"
    echo "  source .venv/bin/activate"
    echo "  semeclaw setup        # interactive: API keys, smoke test, save"
    echo "  semeclaw status       # what's configured / what's missing"
    echo "  semeclaw agents       # the 5 core agents + 4 adapters available"
    echo "  semeclaw live-demo    # 4-agent live demo on a real task (~90s)"
    echo ""
    echo -e "${CYAN}When ready to build${NC}"
    echo "  semeclaw war-room     # start the War Room dashboard"
    echo "  semeclaw chat         # chat directly with the orchestrator"
    echo ""
}

run_onboarding() {
    if [ -t 0 ] && [ -t 1 ]; then
        echo ""
        info "Launching interactive onboarding (semeclaw setup)..."
        cd "$INSTALL_DIR" || return 0
        # shellcheck disable=SC1091
        [ -f .venv/bin/activate ] && source .venv/bin/activate
        semeclaw setup || warn "Setup did not complete — re-run any time with: semeclaw setup"
    else
        info "Non-interactive install — skipping onboarding."
        info "Run later with: semeclaw setup"
    fi
}

# Main
print_banner
check_python
check_uv
clone_repo
install_deps
setup_env
setup_workspace
print_next_steps
run_onboarding
