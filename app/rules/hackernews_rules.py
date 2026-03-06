from __future__ import annotations

import json
import logging

from app.config import settings
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import AIEnrichment, Alert, Severity, SourceType
from app.sources.hackernews import HackerNewsSource

logger = logging.getLogger(__name__)

_CATEGORY_LABELS: dict[str, str] = {
    "ai": "AI",
    "infrastructure": "基础设施",
    "security": "安全",
    "programming": "编程",
    "startup": "创业",
    "open_source": "开源",
    "industry_news": "行业资讯",
    "science": "科学",
    "other": "其他",
}


@rule_registry.register(
    source="hackernews",
    schedule="interval:7200s",
    trigger="batch",
)
async def discover_hn_hot_topics(ctx: RuleContext) -> bool:
    """Discover hot topics from Hacker News front page and rising stories."""
    source = HackerNewsSource()

    try:
        events = await source.fetch()
    except Exception:
        logger.exception("HN fetch failed")
        return False

    if not events:
        logger.info("No HN events fetched")
        await source.stop()
        return False

    logger.info("HN fetched %d stories", len(events))

    # Redis dedup (7-day window)
    fresh: list = []
    for event in events:
        dedup_key = f"hn:story:{event.source_id}:pushed"
        if await ctx.db.exists(dedup_key):
            continue
        fresh.append(event)

    if not fresh:
        logger.info("All HN stories already pushed — nothing new")
        await source.stop()
        return False

    # Sort by points desc, take top N
    fresh.sort(key=lambda e: e.data.get("points", 0), reverse=True)
    top = fresh[: settings.hn_max_stories_per_run]

    logger.info(
        "HN: %d fresh stories, processing top %d",
        len(fresh),
        len(top),
    )

    alerts_created = 0
    all_alerts: list[Alert] = []

    for event in top:
        object_id = event.source_id
        data = event.data

        # Fetch top comments for AI context
        try:
            comments = await source.fetch_item_comments(object_id, limit=5)
        except Exception:
            logger.exception("Failed to fetch comments for %s", object_id)
            comments = []

        # AI analysis
        tmpl_ctx = {
            "title": data.get("title", ""),
            "url": data.get("url", ""),
            "hn_url": data.get("hn_url", ""),
            "points": data.get("points", 0),
            "num_comments": data.get("num_comments", 0),
            "author": data.get("author", ""),
            "created_at": data.get("created_at", ""),
            "discovery_strategy": data.get("discovery_strategy", "unknown"),
            "comments": comments,
        }

        try:
            ai_result = await ctx.ai.analyze(
                "hackernews/topic_analysis.jinja2", tmpl_ctx
            )
        except Exception:
            logger.exception("AI analysis failed for HN story %s", object_id)
            ai_result = {}

        recommendation = ai_result.get("recommendation", "skip")
        logger.info(
            "AI result for HN %s: recommendation=%s, score=%s",
            object_id,
            recommendation,
            ai_result.get("relevance_score", "?"),
        )

        if recommendation == "skip":
            continue

        severity = Severity.HIGH if recommendation == "worth_reading" else Severity.MEDIUM

        try:
            confidence = float(ai_result.get("relevance_score", 0))
        except (ValueError, TypeError):
            confidence = 0.0

        enrichment = AIEnrichment(
            summary=ai_result.get("summary", ""),
            analysis=json.dumps(ai_result, ensure_ascii=False),
            confidence=confidence,
        )

        category = ai_result.get("category", "other")
        category_label = _CATEGORY_LABELS.get(category, category)
        points = data.get("points", 0)

        alert = Alert(
            source=SourceType.HACKERNEWS,
            rule_name="discover_hn_hot_topics",
            severity=severity,
            title=f"[{category_label}] {data.get('title', '')[:80]} ({points}↑)",
            event=event,
            enrichment=enrichment,
        )

        # Store alert
        await ctx.db.lpush("alerts:hackernews", alert.model_dump_json())
        await ctx.db.ltrim("alerts:hackernews", 0, settings.alert_max_per_source - 1)

        # Set dedup key (7-day TTL)
        await ctx.db.set(f"hn:story:{object_id}:pushed", "1", ex=7 * 86400)

        all_alerts.append(alert)

        alerts_created += 1
        logger.info("HN alert: %s (%d points)", data.get("title", "")[:60], points)

    # Send all alerts as a single digest card
    if all_alerts:
        await ctx.delivery.send_batch(all_alerts)

    await source.stop()

    logger.info(
        "HN rule completed: %d alerts from %d candidates",
        alerts_created,
        len(top),
    )
    return alerts_created > 0
