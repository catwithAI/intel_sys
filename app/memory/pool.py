from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from app.config import settings
from app.models import Event, MemoryEvent

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.ai.client import AIClient

logger = logging.getLogger(__name__)

# Number of events per LLM batch call
_BATCH_SIZE = 10


class EventMemoryPool:
    """Redis Sorted Set backed event memory pool.

    Each event is compressed via LLM and stored as a JSON member with
    score = Unix timestamp for efficient time-range queries.
    """

    def __init__(self, redis_client: Redis, ai_client: AIClient) -> None:
        self._db = redis_client
        self._ai = ai_client
        self._key = settings.memory_pool_key

    async def add_events_batch(self, events: list[Event]) -> int:
        """Dedup, compress in batches via LLM, and store. Returns count added."""
        # 1. Dedup
        fresh: list[Event] = []
        for event in events:
            dedup_key = f"memory:dedup:{event.source.value}:{event.source_id}"
            if await self._db.exists(dedup_key):
                continue
            fresh.append(event)

        if not fresh:
            return 0

        # 2. Set dedup keys upfront
        for event in fresh:
            dedup_key = f"memory:dedup:{event.source.value}:{event.source_id}"
            await self._db.set(dedup_key, "1", ex=settings.memory_pool_ttl_days * 86400)

        # 3. Batch compress
        added = 0
        for i in range(0, len(fresh), _BATCH_SIZE):
            batch = fresh[i : i + _BATCH_SIZE]
            mem_events = await self._compress_batch(batch)
            # Store in Redis
            if mem_events:
                mapping = {me.model_dump_json(): me.timestamp for me in mem_events}
                await self._db.zadd(self._key, mapping)
                added += len(mem_events)

        return added

    async def _compress_batch(self, events: list[Event]) -> list[MemoryEvent]:
        """Compress a batch of events in a single LLM call."""
        source = events[0].source.value

        # Build batch input
        batch_items = []
        for event in events:
            raw_text = (
                event.data.get("title", "")
                + " "
                + event.data.get("content", event.data.get("selftext", ""))
            )
            batch_items.append({"id": event.source_id, "text": raw_text[:300]})

        try:
            result = await self._ai.analyze(
                "memory/event_compress.jinja2",
                {"source": source, "events": batch_items},
            )
        except Exception:
            logger.exception("LLM batch compress failed for %d events", len(events))
            # Fallback: store with raw text as summary
            return self._fallback_compress(events)

        if not isinstance(result, dict) or "results" not in result:
            logger.warning("LLM batch returned unexpected format, using fallback")
            return self._fallback_compress(events)

        # Parse results and match back to events
        result_map = {str(r.get("id", "")): r for r in result.get("results", [])}
        mem_events = []
        for event in events:
            r = result_map.get(event.source_id, {})
            raw_text = event.data.get("title", "") or event.data.get("content", "")
            mem_events.append(MemoryEvent(
                id=event.source_id,
                source=event.source,
                title=event.data.get("title", "")[:80],
                summary=r.get("summary", raw_text[:100]),
                category=r.get("category", "other"),
                entities=r.get("entities", []),
                sentiment=r.get("sentiment", "neutral"),
                timestamp=event.timestamp.timestamp(),
                url=event.data.get("url", event.data.get("permalink", "")),
            ))
        return mem_events

    @staticmethod
    def _fallback_compress(events: list[Event]) -> list[MemoryEvent]:
        """Fallback: store events with raw title as summary (no LLM)."""
        mem_events = []
        for event in events:
            raw_text = event.data.get("title", "") or event.data.get("content", "")
            mem_events.append(MemoryEvent(
                id=event.source_id,
                source=event.source,
                title=raw_text[:80],
                summary=raw_text[:100],
                category="other",
                entities=[],
                sentiment="neutral",
                timestamp=event.timestamp.timestamp(),
                url=event.data.get("url", event.data.get("permalink", "")),
            ))
        return mem_events

    async def add_event(self, event: Event) -> MemoryEvent | None:
        """Compress single event via LLM and store. Use add_events_batch for bulk."""
        dedup_key = f"memory:dedup:{event.source.value}:{event.source_id}"
        if await self._db.exists(dedup_key):
            return None
        await self._db.set(dedup_key, "1", ex=settings.memory_pool_ttl_days * 86400)

        raw_text = (
            event.data.get("title", "")
            + " "
            + event.data.get("content", event.data.get("selftext", ""))
        )
        try:
            result = await self._ai.analyze(
                "memory/event_compress.jinja2",
                {"text": raw_text[:1000], "source": event.source.value, "events": None},
            )
        except Exception:
            logger.exception("LLM compress failed for %s:%s", event.source.value, event.source_id)
            result = {}

        ts = event.timestamp.timestamp()
        mem_event = MemoryEvent(
            id=event.source_id,
            source=event.source,
            title=event.data.get("title", "")[:80],
            summary=result.get("summary", raw_text[:100]) if isinstance(result, dict) else raw_text[:100],
            category=result.get("category", "other") if isinstance(result, dict) else "other",
            entities=result.get("entities", []) if isinstance(result, dict) else [],
            sentiment=result.get("sentiment", "neutral") if isinstance(result, dict) else "neutral",
            timestamp=ts,
            url=event.data.get("url", event.data.get("permalink", "")),
        )
        await self._db.zadd(self._key, {mem_event.model_dump_json(): mem_event.timestamp})
        return mem_event

    async def get_recent(self, hours: int = 168) -> list[MemoryEvent]:
        """Get events from last N hours, sorted by time asc."""
        cutoff = time.time() - hours * 3600
        raw = await self._db.zrangebyscore(self._key, cutoff, "+inf")
        events = []
        for r in raw:
            try:
                events.append(MemoryEvent.model_validate_json(r))
            except Exception:
                logger.warning("Failed to parse memory event: %s", r[:100])
        return events

    async def cleanup(self) -> int:
        """Remove events older than TTL."""
        cutoff = time.time() - settings.memory_pool_ttl_days * 86400
        removed = await self._db.zremrangebyscore(self._key, "-inf", cutoff)
        if removed:
            logger.info("Memory pool cleanup: removed %d expired events", removed)
        return removed

    async def count(self) -> int:
        return await self._db.zcard(self._key)
