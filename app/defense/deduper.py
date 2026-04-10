from __future__ import annotations

from typing import Any

from app.defense.models import NormalizedEvent


class Deduper:
    """Two-layer deduplication using Redis: L1 url_hash + L2 content_hash."""

    def __init__(self, redis: Any, ttl: int = 604800) -> None:
        self._redis = redis
        self._ttl = ttl

    async def filter_duplicates(self, events: list[NormalizedEvent]) -> list[NormalizedEvent]:
        """Filter out events whose url_hash or content_hash already exist in Redis."""
        if not events:
            return []

        pipe = self._redis.pipeline()
        has_url: list[bool] = []
        has_content: list[bool] = []

        for event in events:
            url_hash = event.dedup_keys.get("url_hash", "")
            content_hash = event.dedup_keys.get("content_hash", "")

            if url_hash:
                pipe.set(f"defense:dedup:url:{url_hash}", "1", ex=self._ttl, nx=True)
                has_url.append(True)
            else:
                has_url.append(False)

            if content_hash:
                pipe.set(f"defense:dedup:content:{content_hash}", "1", ex=self._ttl, nx=True)
                has_content.append(True)
            else:
                has_content.append(False)

        results = await pipe.execute()

        unique: list[NormalizedEvent] = []
        result_idx = 0
        for i, event in enumerate(events):
            url_is_new = True
            content_is_new = True

            if has_url[i]:
                url_is_new = results[result_idx]
                result_idx += 1
            if has_content[i]:
                content_is_new = results[result_idx]
                result_idx += 1

            # Both layers must be new (not duplicate) to keep the event
            if url_is_new and content_is_new:
                unique.append(event)

        return unique
