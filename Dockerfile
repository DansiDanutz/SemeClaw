# SemeClaw War Room Agent — production image (Phase 2)
# Build:  docker build -t ghcr.io/dansidanutz/semeclaw:0.7.0 .
# Run:    docker run -p 8765:8765 --env-file .env ghcr.io/dansidanutz/semeclaw:0.7.0

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
COPY install.sh setup.sh .env.example ./
COPY README.md INTEGRATION.md SEMECLAW_AGENT_PLAN.md ./

# Data dirs created on first run, but pre-create for volume-mounting
RUN mkdir -p /app/war_room/audio/meetings/saved \
             /app/war_room/audio/scripts \
             /app/war_room/research/saved \
             /app/war_room/logs

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SEMECLAW_PUBLIC_URL=http://0.0.0.0:8765 \
    SEMECLAW_TENANT_ID=default

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/api/agent/health || exit 1

# Ensure the semeclaw CLI is available
RUN uv pip install -e .

CMD ["sh", "-c", "mkdir -p /app/data/audio/meetings/saved /app/data/audio/scripts /app/data/research/saved /app/data/logs /app/data/memory /app/data/builds && ln -sf /app/data/audio /app/war_room/audio 2>/dev/null || true && ln -sf /app/data/research /app/war_room/research 2>/dev/null || true && ln -sf /app/data/logs /app/war_room/logs 2>/dev/null || true && ln -sf /app/data/memory /app/war_room/memory 2>/dev/null || true && ln -sf /app/data/builds /app/war_room/builds 2>/dev/null || true && /usr/local/bin/uv run python -m uvicorn war_room.dashboard.server:app --host 0.0.0.0 --port 8765"]
