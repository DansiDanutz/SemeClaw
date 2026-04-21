FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock* ./
COPY src ./src
COPY nervix_platform ./nervix_platform

# Install package + deps
RUN uv sync --frozen --no-install-project || uv sync --no-install-project
RUN uv pip install -e .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NERVIX_DB_PATH=/app/data/nervix.json

# Data directory for JSON DB (mount a Fly volume here)
RUN mkdir -p /app/data

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/api/health || exit 1

CMD ["uv", "run", "python", "-m", "uvicorn", "nervix_platform.main:app", "--host", "0.0.0.0", "--port", "8001"]
