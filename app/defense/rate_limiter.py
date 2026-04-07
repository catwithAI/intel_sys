from __future__ import annotations

import asyncio
import time


class DomainRateLimiter:
    """Per-domain rate limiter using asyncio.Lock + monotonic time."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def wait_if_needed(self, domain: str, min_interval: float) -> None:
        lock = self._get_lock(domain)
        async with lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request[domain] = time.monotonic()
