from __future__ import annotations

import logging

from app.config import settings
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.sources.reddit import RedditSource

logger = logging.getLogger(__name__)


@rule_registry.register(
    source="reddit",
    schedule="interval:1800s",
    trigger="batch",
)
async def ingest_reddit_posts(ctx: RuleContext) -> bool:
    """采集 Reddit 热门帖子，压缩后写入记忆池。不产生 Alert。"""
    if not settings.reddit_client_id:
        logger.info("Reddit not configured (REDDIT_CLIENT_ID empty), skipping")
        return False

    source = RedditSource()
    pool = EventMemoryPool(ctx.db, ctx.ai)

    try:
        events = await source.fetch()
        added = await pool.add_events_batch(events)
        logger.info("Reddit ingest: %d fetched, %d added to memory pool", len(events), added)
    finally:
        await source.stop()

    return False  # Never generates alerts directly
