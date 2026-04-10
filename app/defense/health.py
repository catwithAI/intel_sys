from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_HOURS = 6
DEFAULT_COOLING_THRESHOLD = 3
DEFAULT_DISABLE_THRESHOLD = 10


class SourceHealthManager:
    """Manages source health state machine: ok → cooling_down → pending_disable."""

    def __init__(
        self,
        storage: Any,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        cooling_threshold: int = DEFAULT_COOLING_THRESHOLD,
        disable_threshold: int = DEFAULT_DISABLE_THRESHOLD,
    ) -> None:
        self._storage = storage
        self._cooldown_hours = cooldown_hours
        self._cooling_threshold = cooling_threshold
        self._disable_threshold = disable_threshold
        self._cache: dict[str, dict] = {}
        self._pending_recovery: list[str] = []

    async def refresh_cache(self) -> None:
        """Reload health data from storage."""
        records = await self._storage.get_source_health()
        self._cache = {r["site_id"]: r for r in records}

    async def flush_recovery(self) -> None:
        """Write back any lazy recovery state changes to storage."""
        for site_id in self._pending_recovery:
            await self._storage.upsert_source_health(site_id, {
                "status": "ok",
                "consecutive_failures": 0,
            })
        self._pending_recovery.clear()

    def is_available(self, site_id: str) -> bool:
        """Check if source is available. Implements lazy recovery for cooling_down."""
        record = self._cache.get(site_id)
        if not record:
            return True

        status = record.get("status", "ok")
        if status == "ok":
            return True
        if status in ("pending_disable", "disabled"):
            return False

        # cooling_down: check if cooldown period has passed
        if status == "cooling_down":
            cooldown_until = record.get("cooldown_until")
            if cooldown_until and cooldown_until < datetime.now(timezone.utc):
                # Lazy recovery: update cache + queue for PG write-back
                record["status"] = "ok"
                record["consecutive_failures"] = 0
                self._pending_recovery.append(site_id)
                return True
            return False

        return False

    async def record_success(self, site_id: str) -> None:
        """Record a successful fetch."""
        record = self._cache.get(site_id, {})
        total_fetches = record.get("total_fetches", 0) + 1

        payload = {
            "status": "ok",
            "last_success_at": datetime.now(timezone.utc),
            "consecutive_failures": 0,
            "total_fetches": total_fetches,
        }
        await self._storage.upsert_source_health(site_id, payload)
        # Merge into cache instead of replacing
        if site_id in self._cache:
            self._cache[site_id].update(payload)
        else:
            self._cache[site_id] = {"site_id": site_id, **payload}

    async def record_failure(self, site_id: str, error: str) -> None:
        """Record a failed fetch. Transitions state machine."""
        record = self._cache.get(site_id, {})
        failures = record.get("consecutive_failures", 0) + 1
        total_fetches = record.get("total_fetches", 0) + 1
        total_failures = record.get("total_failures", 0) + 1

        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "last_failure_at": now,
            "last_error": error,
            "consecutive_failures": failures,
            "total_fetches": total_fetches,
            "total_failures": total_failures,
        }

        if failures >= self._disable_threshold:
            payload["status"] = "pending_disable"
        elif failures >= self._cooling_threshold:
            payload["status"] = "cooling_down"
            payload["cooldown_until"] = now + timedelta(hours=self._cooldown_hours)
        else:
            payload["status"] = record.get("status", "ok")

        await self._storage.upsert_source_health(site_id, payload)
        # Merge into cache
        if site_id in self._cache:
            self._cache[site_id].update(payload)
        else:
            self._cache[site_id] = {"site_id": site_id, **payload}
