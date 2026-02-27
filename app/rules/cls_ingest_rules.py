from __future__ import annotations

import logging

from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.sources.cls_news import CLSNewsSource

logger = logging.getLogger(__name__)


@rule_registry.register(
    source="cls",
    schedule="interval:120s",
    trigger="batch",
)
async def ingest_cls_news(ctx: RuleContext) -> bool:
    """采集财联社电报，压缩后写入记忆池。不产生 Alert。"""
    source = CLSNewsSource()
    pool = EventMemoryPool(ctx.db, ctx.ai)

    try:
        events = await source.fetch()
        added = await pool.add_events_batch(events)
        logger.info("CLS ingest: %d fetched, %d added to memory pool", len(events), added)
    finally:
        await source.stop()

    return False  # Never generates alerts directly
