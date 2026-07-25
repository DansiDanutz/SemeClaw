.PHONY: help install dev demo test lint format run dashboard docker-build docker-run clean

help:
	@echo "SemeClaw — Available commands:"
	@echo "  make install      Install dependencies with uv"
	@echo "  make dev          Install + start dashboard in dev mode"
	@echo "  make demo         Run the welcome demo (no API keys)"
	@echo "  make test         Run all tests"
	@echo "  make lint         Run ruff linter"
	@echo "  make format       Run ruff formatter"
	@echo "  make run          Start SemeClaw chat"
	@echo "  make dashboard    Start War Room dashboard"
	@echo "  make docker-build Build Docker image"
	@echo "  make docker-run   Run Docker container"
	@echo "  make clean        Remove generated state and caches"

install:
	uv sync --dev

dev: install
	@echo "🚀 Starting War Room dashboard on http://127.0.0.1:8765"
	uv run semeclaw war-room

demo:
	uv run semeclaw demo

test:
	uv run pytest war_room/tests/ tests/ -v

lint:
	uv run ruff check src/ war_room/

format:
	uv run ruff format src/ war_room/

run:
	uv run semeclaw chat

dashboard:
	uv run semeclaw war-room

docker-build:
	docker build -t semeclaw:latest .

docker-run:
	docker run -p 8765:8765 -v $(PWD)/default_workspace:/app/default_workspace:ro semeclaw:latest

clean:
	rm -rf war_room/logs/* war_room/audio_cache/* war_room/*.json war_room/*.json.lock
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
