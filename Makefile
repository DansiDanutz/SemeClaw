.PHONY: help install test lint run dashboard docker-build docker-run clean

help:
	@echo "SemeClaw — Available commands:"
	@echo "  make install      Install dependencies with uv"
	@echo "  make test         Run all tests"
	@echo "  make run          Start SemeClaw chat"
	@echo "  make dashboard    Start War Room dashboard"
	@echo "  make docker-build Build Docker image"
	@echo "  make docker-run   Run Docker container"
	@echo "  make clean        Remove generated state and caches"

install:
	uv sync --all-extras --dev

test:
	uv run pytest war_room/tests/ -v

run:
	uv run semeclaw chat

dashboard:
	python war_room/dashboard/server.py

docker-build:
	docker build -t semeclaw:latest .

docker-run:
	docker run -p 8765:8765 -v $(PWD)/default_workspace:/app/default_workspace:ro semeclaw:latest

clean:
	rm -rf war_room/logs/* war_room/audio_cache/* war_room/*.json war_room/*.json.lock
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
