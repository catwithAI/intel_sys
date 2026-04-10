from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
import re

from app.config import settings
from app.delivery.feishu import FeishuWebhookDelivery, NoopDelivery
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.models import AIEnrichment, Alert, Event, MemoryEvent, Severity, SourceType

logger = logging.getLogger(__name__)


def _normalize_key(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _dedup_and_merge_insights(insights: list[dict]) -> list[dict]:
    """Dedup same-day insights by investment direction first, then title.

    Many correlation outputs vary in wording but point to the same investment
    direction. We merge those before delivery to reduce repetitive pushes.
    """
    grouped: dict[str, dict] = {}

    for insight in insights:
        title = str(insight.get("title", "")).strip()
        direction = str(insight.get("investment_direction", "")).strip()
        if not title:
            continue

        key = _normalize_key(direction) or _normalize_key(title)
        if not key:
            continue

        current_conf = float(insight.get("confidence", 0.5) or 0.5)
        existing = grouped.get(key)
        if not existing:
            merged = dict(insight)
            merged["_merged_titles"] = [title]
            merged["_merged_reasonings"] = [str(insight.get("reasoning", "")).strip()]
            merged["_merged_count"] = 1
            grouped[key] = merged
            continue

        existing_conf = float(existing.get("confidence", 0.5) or 0.5)
        if current_conf > existing_conf:
            preserved_titles = existing.get("_merged_titles", [])
            preserved_reasonings = existing.get("_merged_reasonings", [])
            merged_count = existing.get("_merged_count", 1)
            replacement = dict(insight)
            replacement["_merged_titles"] = preserved_titles
            replacement["_merged_reasonings"] = preserved_reasonings
            replacement["_merged_count"] = merged_count
            existing = replacement
            grouped[key] = existing

        titles = existing.setdefault("_merged_titles", [])
        if title not in titles:
            titles.append(title)

        reasoning = str(insight.get("reasoning", "")).strip()
        if reasoning:
            reasonings = existing.setdefault("_merged_reasonings", [])
            if reasoning not in reasonings:
                reasonings.append(reasoning)

        merged_chain = existing.setdefault("chain", [])
        for item in insight.get("chain", []) or []:
            if item not in merged_chain:
                merged_chain.append(item)

        merged_assets = existing.setdefault("related_assets", [])
        seen_symbols = {
            str(a.get("symbol", "")).strip().upper()
            for a in merged_assets
            if isinstance(a, dict)
        }
        for asset in insight.get("related_assets", []) or []:
            if not isinstance(asset, dict):
                continue
            symbol = str(asset.get("symbol", "")).strip().upper()
            if symbol and symbol not in seen_symbols:
                merged_assets.append(asset)
                seen_symbols.add(symbol)

        existing["_merged_count"] = len(existing.get("_merged_titles", []))

    merged_insights = list(grouped.values())
    merged_insights.sort(key=lambda x: float(x.get("confidence", 0.5) or 0.5), reverse=True)
    return merged_insights


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


async def _send_insights(alerts: list[Alert]) -> None:
    """Send insights via dedicated webhook as a single digest when possible."""
    if not settings.feishu_insight_webhook_url:
        logger.info("Insight webhook not configured, skipping delivery")
        return

    delivery = FeishuWebhookDelivery(
        settings.feishu_insight_webhook_url,
        settings.feishu_insight_webhook_secret,
    )
    try:
        await delivery.send_batch(alerts)
    finally:
        await delivery.close()


@rule_registry.register(
    source="correlation",
    schedule="cron:30 15 * * *",
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

    insights = _dedup_and_merge_insights(insights)
    logger.info("Correlation: deduped to %d merged insights", len(insights))

    # Create alerts for each insight
    alerts_created = 0
    all_alerts: list[Alert] = []
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
                "title": title if insight.get("_merged_count", 1) <= 1 else f"{title} 等 {insight.get('_merged_count', 1)} 条",
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
                "merged_titles": insight.get("_merged_titles", []),
                "merged_count": insight.get("_merged_count", 1),
            },
        )

        confidence = float(insight.get("confidence", 0.5))
        severity = Severity.HIGH if confidence >= 0.7 else Severity.MEDIUM

        alert = Alert(
            source=SourceType.CORRELATION,
            rule_name="discover_cross_event_insights",
            severity=severity,
            title=event.data.get("title", title),
            event=event,
            enrichment=AIEnrichment(
                summary="\n".join((insight.get("_merged_reasonings", []) or [insight.get("reasoning", "")])[:3]).strip(),
                analysis=json.dumps(insight, ensure_ascii=False),
                confidence=confidence,
            ),
        )

        # Store alert
        alert_json = alert.model_dump_json()
        await ctx.db.lpush("alerts:correlation", alert_json)
        await ctx.db.ltrim("alerts:correlation", 0, settings.alert_max_per_source - 1)

        all_alerts.append(alert)
        alerts_created += 1

    if all_alerts:
        await _send_insights(all_alerts)

    logger.info(
        "Correlation: %d insights from %d events", alerts_created, len(events)
    )
    return alerts_created > 0
