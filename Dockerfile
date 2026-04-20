# SemeClaw — Docker image
# Phase 2: Deploy + CI (ghcr.io, GitHub Actions, release tags)
#
# Build:
#   docker build -t semeclaw:0.7.0 .
# Run:
#   docker run -p 8765:8765 --env-file .env semeclaw:0.7.0

FROM python:3.13-slim

LABEL org.opencontainers.image.title="SemeClaw"
LABEL org.opencontainers.image.description="Self-hosted AI agent that turns any task report into a cinematic multi-agent meeting."
LABEL org.opencontainers.image.source="https://github.com/DansiDanutz/SemeClaw"

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency specs first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv (falls back to pip if lockfile stale)
RUN uv sync --locked --no-dev || uv pip install -e .

# Copy application code
COPY src/ ./src/
COPY war_room/ ./war_room/
COPY default_workspace/ ./default_workspace/
COPY README.md ./

# Install the package itself in editable mode so `semeclaw` CLI is available
RUN uv pip install -e .

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/tts/health')" || exit 1

# Expose ports
# 8765 — War Room Dashboard (Flask)
EXPOSE 8765

# Default: start the War Room dashboard
CMD ["python", "war_room/dashboard/server.py", "8765"]
