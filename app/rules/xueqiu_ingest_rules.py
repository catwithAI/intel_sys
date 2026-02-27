from __future__ import annotations

import logging

from app.config import settings
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.sources.xueqiu import XueqiuSource

logger = logging.getLogger(__name__)


@rule_registry.register(
    source="xueqiu",
    schedule="interval:300s",
    trigger="batch",
)
async def ingest_xueqiu_news(ctx: RuleContext) -> bool:
    """采集雪球 7x24 快讯，压缩后写入记忆池。不产生 Alert。"""
    if not settings.xueqiu_cookie:
        logger.info("Xueqiu not configured (XUEQIU_COOKIE empty), skipping")
        return False

    source = XueqiuSource()
    pool = EventMemoryPool(ctx.db, ctx.ai)

    try:
        events = await source.fetch()
        added = await pool.add_events_batch(events)
        logger.info("Xueqiu ingest: %d fetched, %d added to memory pool", len(events), added)
    finally:
        await source.stop()

    return False  # Never generates alerts directly
