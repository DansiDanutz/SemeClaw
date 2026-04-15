"""Cron job loader for scheduled tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class CronDef(BaseModel):
    """Definition of a cron job."""

    id: str
    schedule: str  # Cron expression (e.g., "0 9 * * *")
    agent: str  # Agent to dispatch to
    prompt: str  # Prompt/content to send to agent
    description: str = ""
    enabled: bool = True

    class Config:
        """Pydantic config."""

        extra = "allow"


class CronLoader:
    """Loader for discovering and managing cron jobs."""

    def __init__(self, crons_path: Path):
        """Initialize the cron loader.

        Args:
            crons_path: Path to the crons directory.
        """
        self.crons_path = crons_path
        self.logger = logging.getLogger(__name__)
        self._crons: dict[str, CronDef] = {}
        self._load_crons()

    def _load_crons(self) -> None:
        """Load all cron definitions from YAML files."""
        if not self.crons_path.exists():
            self.logger.warning(f"Crons directory not found: {self.crons_path}")
            return

        try:
            for cron_file in self.crons_path.glob("*.yaml"):
                try:
                    with open(cron_file) as f:
                        data = yaml.safe_load(f) or {}

                    cron_def = CronDef(**data)
                    self._crons[cron_def.id] = cron_def
                    self.logger.info(f"Loaded cron: {cron_def.id} ({cron_def.schedule})")
                except Exception as e:
                    self.logger.exception(f"Failed to load cron from {cron_file}: {e}")
        except Exception as e:
            self.logger.exception(f"Failed to scan crons directory: {e}")

    def discover_crons(self) -> dict[str, CronDef]:
        """Get all discovered cron definitions.

        Returns:
            Dictionary of cron_id -> CronDef.
        """
        return {cid: cron for cid, cron in self._crons.items() if cron.enabled}

    def get_cron(self, cron_id: str) -> CronDef | None:
        """Get a specific cron definition.

        Args:
            cron_id: The cron ID.

        Returns:
            The CronDef, or None if not found.
        """
        return self._crons.get(cron_id)

    def reload(self) -> None:
        """Reload all cron definitions from disk."""
        self._crons.clear()
        self._load_crons()
        self.logger.info("Cron definitions reloaded")

    def __len__(self) -> int:
        """Get the number of loaded crons."""
        return len(self._crons)

    def __repr__(self) -> str:
        """Get string representation."""
        return f"CronLoader({len(self._crons)} crons)"
