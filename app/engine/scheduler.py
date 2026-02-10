from __future__ import annotations

import logging
import re
from typing import Callable, Coroutine, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.engine.registry import RuleMeta

logger = logging.getLogger(__name__)


def parse_schedule(schedule: str) -> dict:
    """Parse schedule string like 'interval:30s' or 'cron:0 9 * * *' into APScheduler kwargs."""
    if schedule.startswith("interval:"):
        value = schedule[len("interval:"):]
        match = re.match(r"(\d+)(s|m|h)", value)
        if not match:
            raise ValueError(f"Invalid interval format: {value}")
        amount, unit = int(match.group(1)), match.group(2)
        unit_map = {"s": "seconds", "m": "minutes", "h": "hours"}
        return {"trigger": "interval", unit_map[unit]: amount}

    if schedule.startswith("cron:"):
        cron_expr = schedule[len("cron:"):]
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron format (need 5 fields): {cron_expr}")
        minute, hour, day, month, day_of_week = parts
        return {
            "trigger": "cron",
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "day_of_week": day_of_week,
        }

    raise ValueError(f"Unknown schedule type: {schedule}")


class Scheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def register_rule(
        self,
        meta: RuleMeta,
        job_fn: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Register a rule as a scheduled job."""
        try:
            kwargs = parse_schedule(meta.schedule)
        except ValueError:
            logger.exception("Bad schedule for rule %s", meta.name)
            return

        trigger = kwargs.pop("trigger")
        self._scheduler.add_job(
            job_fn,
            trigger=trigger,
            id=f"rule:{meta.name}",
            name=meta.name,
            replace_existing=True,
            **kwargs,
        )
        logger.info("Scheduled rule %s: %s", meta.name, meta.schedule)

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def pause_job(self, job_id: str) -> bool:
        try:
            self._scheduler.pause_job(job_id)
            logger.info("Paused job: %s", job_id)
            return True
        except Exception:
            logger.warning("Job not found: %s", job_id)
            return False

    def resume_job(self, job_id: str) -> bool:
        try:
            self._scheduler.resume_job(job_id)
            logger.info("Resumed job: %s", job_id)
            return True
        except Exception:
            logger.warning("Job not found: %s", job_id)
            return False

    @property
    def jobs(self) -> list:
        return self._scheduler.get_jobs()
