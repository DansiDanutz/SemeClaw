"""Cron worker — schedules and dispatches cron jobs using real cron expressions."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from semeclaw.core.events import CronEventSource, InboundEvent
from semeclaw.server.worker import Worker

if TYPE_CHECKING:
    from semeclaw.core.cron_loader import CronLoader
    from semeclaw.core.eventbus import EventBus


class CronWorker(Worker):
    """Worker that manages scheduled cron jobs using real cron expressions."""

    TICK_INTERVAL = 60  # check every minute

    def __init__(self, eventbus: EventBus, cron_loader: CronLoader):
        super().__init__()
        self.eventbus = eventbus
        self.cron_loader = cron_loader
        self.logger = logging.getLogger(__name__)
        self._last_run: dict[str, datetime] = {}

    async def run(self) -> None:
        """Run the cron worker — ticks every minute and fires due jobs."""
        self.logger.info(f"CronWorker starting with {len(self.cron_loader)} crons")

        try:
            while self._running:
                await self._check_due_crons()
                await asyncio.sleep(self.TICK_INTERVAL)
        except asyncio.CancelledError:
            self.logger.info("CronWorker cancelled")
            raise

    async def _check_due_crons(self) -> None:
        crons = self.cron_loader.discover_crons()
        now = datetime.now()

        for cron_id, cron_def in crons.items():
            try:
                if self._is_due(cron_id, cron_def.schedule, now):
                    await self._dispatch_cron(cron_id, cron_def)
            except Exception as e:
                self.logger.exception(f"Error checking cron {cron_id}: {e}")

    def _is_due(self, cron_id: str, schedule: str, now: datetime) -> bool:
        """Check whether a cron is due using croniter for real cron expressions."""
        try:
            from croniter import croniter

            last_run = self._last_run.get(cron_id)
            if last_run is None:
                # Never run — check if it should have run in the last tick window
                cron = croniter(schedule, now)
                prev = cron.get_prev(datetime)
                # Due if scheduled time was within the last TICK_INTERVAL seconds
                return (now - prev).total_seconds() <= self.TICK_INTERVAL

            # Already ran — check if next scheduled time is now or past
            cron = croniter(schedule, last_run)
            next_run = cron.get_next(datetime)
            return now >= next_run

        except ImportError:
            # croniter not installed — fall back to daily (23h) check
            last = self._last_run.get(cron_id)
            if last is None:
                return True
            return (now - last).total_seconds() > 82800

        except Exception as e:
            self.logger.warning(f"Schedule parse error for {cron_id} ('{schedule}'): {e}")
            return False

    async def _dispatch_cron(self, cron_id: str, cron_def) -> None:
        """Dispatch a cron job as an InboundEvent targeting the right agent."""
        try:
            source = CronEventSource(cron_id=cron_id)
            session_id = f"cron-{cron_id}-{uuid.uuid4().hex[:8]}"

            # Use InboundEvent so AgentWorker routes it to the correct agent
            # (stored in cron_def.agent, used by AgentWorker to select the agent)
            event = InboundEvent(
                session_id=session_id,
                source=source,
                content=cron_def.prompt,
            )
            # Attach target agent to the event so AgentWorker can read it
            event.target_agent = getattr(cron_def, "agent", None)

            await self.eventbus.publish(event)
            self._last_run[cron_id] = datetime.now()

            self.logger.info(f"Dispatched cron '{cron_id}' → agent '{event.target_agent}'")
        except Exception as e:
            self.logger.exception(f"Failed to dispatch cron {cron_id}: {e}")
