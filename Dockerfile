# SemeClaw War Room Agent — production image (Phase 2)
# Build:  docker build -t ghcr.io/dansidanutz/semeclaw:0.6.0 .
# Run:    docker run -p 8765:8765 --env-file .env ghcr.io/dansidanutz/semeclaw:0.6.0

FROM python:3.13-slim AS base

# System deps: ffmpeg for meeting audio concat, curl for healthcheck, git for uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy manifest first for layer cache
COPY pyproject.toml uv.lock* ./

# Install deps
RUN uv sync --frozen --no-install-project || uv sync --no-install-project

# Copy source
COPY src ./src
COPY war_room ./war_room
COPY default_workspace ./default_workspace
COPY README.md INTEGRATION.md SEMECLAW_AGENT_PLAN.md ./

# Data dirs created on first run, but pre-create for volume-mounting
RUN mkdir -p /app/war_room/audio/meetings/saved \
             /app/war_room/audio/scripts \
             /app/war_room/research/saved \
             /app/war_room/logs

ENV PYTHONUNBUFFERED=1 \
    SEMECLAW_PUBLIC_URL=http://0.0.0.0:8765 \
    SEMECLAW_TENANT_ID=default

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/api/agent/health || exit 1

CMD ["uv", "run", "python", "war_room/dashboard/server.py"]
