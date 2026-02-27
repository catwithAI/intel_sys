from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from app.config import settings
from app.delivery.feishu import FeishuWebhookDelivery, NoopDelivery
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.models import AIEnrichment, Alert, Event, MemoryEvent, Severity, SourceType

logger = logging.getLogger(__name__)


def _build_event_digest(events: list[MemoryEvent], max_chars: int) -> str:
    """Build a time-grouped, category-aggregated digest for LLM context.

    Groups: today / yesterday / 2 days ago / earlier.
    Within each group, events are listed chronologically.
    Truncated to fit within max_chars.
    """
    now = datetime.now(timezone.utc)
    groups: dict[str, list[MemoryEvent]] = {
        "today": [],
        "yesterday": [],
        "2_days_ago": [],
        "earlier": [],
    }
    group_labels = {
        "today": "== 今天 ==",
        "yesterday": "== 昨天 ==",
        "2_days_ago": "== 前天 ==",
        "earlier": "== 更早 ==",
    }

    for ev in events:
        ev_dt = datetime.fromtimestamp(ev.timestamp, tz=timezone.utc)
        delta_days = (now - ev_dt).days
        if delta_days == 0:
            groups["today"].append(ev)
        elif delta_days == 1:
            groups["yesterday"].append(ev)
        elif delta_days == 2:
            groups["2_days_ago"].append(ev)
        else:
            groups["earlier"].append(ev)

    lines: list[str] = []
    total_len = 0

    for key in ["today", "yesterday", "2_days_ago", "earlier"]:
        group_events = groups[key]
        if not group_events:
            continue

        header = group_labels[key]
        lines.append(header)
        total_len += len(header) + 1

        for ev in group_events:
            src_tag = ev.source.value.upper()
            cat_tag = f"[{ev.category}]" if ev.category else ""
            entities_str = ", ".join(ev.entities[:3]) if ev.entities else ""
            sentiment_tag = {"positive": "+", "negative": "-", "neutral": "~"}.get(
                ev.sentiment, "~"
            )

            line = f"[{src_tag}]{cat_tag}({sentiment_tag}) {ev.summary}"
            if entities_str:
                line += f" | {entities_str}"

            if total_len + len(line) + 1 > max_chars:
                lines.append("... (更多事件被截断)")
                return "\n".join(lines)

            lines.append(line)
            total_len += len(line) + 1

    return "\n".join(lines)


async def _send_insight(alert: Alert) -> None:
    """Send insight via dedicated webhook. Creates and disposes its own client."""
    if not settings.feishu_insight_webhook_url:
        logger.info("Insight webhook not configured, skipping delivery")
        return

    delivery = FeishuWebhookDelivery(
        settings.feishu_insight_webhook_url,
        settings.feishu_insight_webhook_secret,
    )
    try:
        await delivery.send(alert)
    finally:
        await delivery.close()


@rule_registry.register(
    source="correlation",
    schedule="interval:1800s",
    trigger="batch",
)
async def discover_cross_event_insights(ctx: RuleContext) -> bool:
    """从记忆池读取事件，LLM 发现跨事件关联和投资洞察。"""
    pool = EventMemoryPool(ctx.db, ctx.ai)

    # Cleanup expired events
    await pool.cleanup()

    # Check minimum events
    count = await pool.count()
    if count < settings.correlation_min_events:
        logger.info(
            "Memory pool has %d events (min %d), skipping",
            count,
            settings.correlation_min_events,
        )
        return False

    events = await pool.get_recent(hours=settings.correlation_lookback_hours)

    # Build context digest
    digest = _build_event_digest(
        events, max_chars=settings.correlation_context_max_chars
    )

    # LLM reasoning
    try:
        result = await ctx.ai.analyze(
            "correlation/cross_event_reasoning.jinja2",
            {
                "event_digest": digest,
                "event_count": len(events),
                "window_days": settings.correlation_lookback_hours // 24,
            },
        )
    except Exception:
        logger.exception("Correlation LLM reasoning failed")
        return False

    insights = result.get("insights", []) if isinstance(result, dict) else []
    if not insights:
        logger.info("Correlation: no insights discovered from %d events", len(events))
        return False

    # Create alerts for each insight
    alerts_created = 0
    for insight in insights:
        title = insight.get("title", "")
        if not title:
            continue

        # Dedup
        dedup_hash = hashlib.md5(title.encode()).hexdigest()[:12]
        dedup_key = f"correlation:insight:{dedup_hash}"
        if await ctx.db.exists(dedup_key):
            continue
        await ctx.db.set(dedup_key, "1", ex=86400)

        event = Event(
            source=SourceType.CORRELATION,
            source_id=dedup_key,
            data={
                "title": title,
                "chain": insight.get("chain", []),
                "reasoning": insight.get("reasoning", ""),
                "investment_direction": insight.get("investment_direction", ""),
                "confidence": insight.get("confidence", 0.5),
                "category": insight.get("category", ""),
                "timeframe": insight.get("timeframe", ""),
                "risks": insight.get("risks", ""),
                "cycle_phase": insight.get("cycle_phase", ""),
                "crowdedness": insight.get("crowdedness", 0),
                "marginal_signals": insight.get("marginal_signals", {}),
                "related_assets": insight.get("related_assets", []),
                "next_catalyst": insight.get("next_catalyst", {}),
            },
        )

        confidence = float(insight.get("confidence", 0.5))
        severity = Severity.HIGH if confidence >= 0.7 else Severity.MEDIUM

        alert = Alert(
            source=SourceType.CORRELATION,
            rule_name="discover_cross_event_insights",
            severity=severity,
            title=title,
            event=event,
            enrichment=AIEnrichment(
                summary=insight.get("reasoning", ""),
                analysis=json.dumps(insight, ensure_ascii=False),
                confidence=confidence,
            ),
        )

        # Store alert
        alert_json = alert.model_dump_json()
        await ctx.db.lpush("alerts:correlation", alert_json)
        await ctx.db.ltrim("alerts:correlation", 0, settings.alert_max_per_source - 1)

        # Send via insight delivery (独立 webhook)
        await _send_insight(alert)
        alerts_created += 1

    logger.info(
        "Correlation: %d insights from %d events", alerts_created, len(events)
    )
    return alerts_created > 0
