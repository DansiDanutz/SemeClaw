# Contributing to SemeClaw

Thank you for your interest in contributing to SemeClaw! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/DansiDanutz/SemeClaw.git`
3. Set up the development environment (see README.md)

## Development Setup

```bash
cd SemeClaw
cp .env.example .env
# Fill in required API keys in .env
uv sync
```

## Running Tests

```bash
uv run pytest tests/ -q
uv run pytest war_room/tests/ -q
```

## Code Style

- Follow PEP 8
- Use type hints where possible
- Keep functions focused and under 100 lines
- Add docstrings for public functions and classes

## Submitting Changes

1. Create a feature branch: `git checkout -b feat/your-feature`
2. Make your changes with clear, atomic commits
3. Ensure tests pass
4. Update documentation if needed
5. Open a Pull Request with a clear description

## Commit Message Convention

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `chore:` Maintenance tasks
- `refactor:` Code refactoring
- `test:` Test additions/changes

## Questions?

Open an issue or reach out via the project Telegram channel.
