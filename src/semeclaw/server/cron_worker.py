"""Cron worker for scheduling and dispatching cron jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from semeclaw.server.worker import Worker
from semeclaw.core.events import DispatchEvent, CronEventSource

if TYPE_CHECKING:
    from semeclaw.core.cron_loader import CronLoader
    from semeclaw.core.eventbus import EventBus


class CronWorker(Worker):
    """Worker that manages scheduled cron jobs."""

    # Check for due jobs every N seconds
    TICK_INTERVAL = 60

    def __init__(self, eventbus: EventBus, cron_loader: CronLoader):
        """Initialize the cron worker.

        Args:
            eventbus: The event bus.
            cron_loader: The cron loader for discovering jobs.
        """
        super().__init__()
        self.eventbus = eventbus
        self.cron_loader = cron_loader
        self.logger = logging.getLogger(__name__)
        self._last_run: dict[str, float] = {}

    async def run(self) -> None:
        """Run the cron worker.

        Periodically checks for due jobs and dispatches them.
        """
        self.logger.info(f"CronWorker starting with {len(self.cron_loader)} crons")

        try:
            while self._running:
                await self._check_due_crons()
                await asyncio.sleep(self.TICK_INTERVAL)
        except asyncio.CancelledError:
            self.logger.info("CronWorker cancelled")
            raise

    async def _check_due_crons(self) -> None:
        """Check for cron jobs that are due and dispatch them."""
        crons = self.cron_loader.discover_crons()
        current_time = time.time()

        for cron_id, cron_def in crons.items():
            try:
                if self._is_due(cron_id, cron_def.schedule, current_time):
                    await self._dispatch_cron(cron_id, cron_def)
            except Exception as e:
                self.logger.exception(f"Error checking cron {cron_id}: {e}")

    def _is_due(self, cron_id: str, schedule: str, current_time: float) -> bool:
        """Check if a cron job is due to run.

        Args:
            cron_id: The cron ID.
            schedule: The cron schedule expression.
            current_time: Current Unix timestamp.

        Returns:
            True if the cron is due to run.
        """
        # Very simplified cron check - in production would use croniter
        # This is a placeholder that assumes daily crons
        last_run = self._last_run.get(cron_id, 0)

        # Simple check: if last run was more than 23 hours ago, due for daily crons
        if current_time - last_run > 82800:  # 23 hours
            return True

        return False

    async def _dispatch_cron(self, cron_id: str, cron_def) -> None:
        """Dispatch a cron job.

        Args:
            cron_id: The cron ID.
            cron_def: The cron definition.
        """
        try:
            source = CronEventSource(cron_id=cron_id)

            event = DispatchEvent(
                session_id=f"cron-{cron_id}-{time.time()}",
                source=source,
                content=cron_def.prompt,
                parent_session_id="",
            )

            await self.eventbus.publish(event)
            self._last_run[cron_id] = time.time()

            self.logger.info(f"Dispatched cron job: {cron_id} to agent {cron_def.agent}")
        except Exception as e:
            self.logger.exception(f"Failed to dispatch cron {cron_id}: {e}")
