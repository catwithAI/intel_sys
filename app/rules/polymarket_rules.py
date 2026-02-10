from __future__ import annotations

import json
import logging

from app.config import settings
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import AIEnrichment, Alert, Severity, SourceType
from app.sources.polymarket import PolymarketSource

logger = logging.getLogger(__name__)


def _calc_book_imbalance(orderbook: dict) -> float:
    """Calculate bid/ask imbalance ratio from top N levels."""
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    total_bid = sum(float(b.get("size", 0)) for b in bids[:10])
    total_ask = sum(float(a.get("size", 0)) for a in asks[:10])
    total = total_bid + total_ask

    if total == 0:
        return 0.5
    return total_bid / total


def _calc_volume_ratio(volume_24h: float, avg_volume_7d: float) -> float:
    """Calculate volume spike ratio."""
    if avg_volume_7d <= 0:
        return 0.0
    return volume_24h / avg_volume_7d


async def _calc_price_velocity(ctx: RuleContext, condition_id: str, current_price: float) -> float:
    """Calculate price change percentage vs stored snapshot."""
    key = f"pm:market:{condition_id}:last_price"
    last_price_str = await ctx.db.get(key)

    # Store current price
    await ctx.db.set(key, str(current_price), ex=3600)

    if last_price_str is None:
        return 0.0

    last_price = float(last_price_str)
    if last_price == 0:
        return 0.0

    return abs(current_price - last_price) / last_price * 100


@rule_registry.register(
    source="polymarket",
    schedule="interval:180s",
    trigger="threshold",
)
async def detect_polymarket_anomalies(ctx: RuleContext) -> bool:
    """Detect volume spikes, orderbook imbalances, and price velocity anomalies."""
    source = PolymarketSource()

    try:
        events = await source.fetch()
    finally:
        await source.stop()

    if not events:
        logger.info("No Polymarket events fetched")
        return False

    alerts_created = 0

    for event in events:
        condition_id = event.data.get("condition_id", "")
        volume_24h = event.data.get("volume_24h", 0)
        orderbook = event.data.get("orderbook", {})
        price = event.data.get("midpoint_price", 0)
        question = event.data.get("question", "")

        # Get baseline from Redis
        baseline_key = f"pm:market:{condition_id}:baseline"
        baseline_str = await ctx.db.get(baseline_key)
        avg_volume_7d = 0.0
        if baseline_str:
            try:
                baseline = json.loads(baseline_str)
                avg_volume_7d = baseline.get("avg_volume_7d", 0)
            except json.JSONDecodeError:
                pass

        # Update baseline (simple exponential moving average)
        if volume_24h > 0:
            new_avg = avg_volume_7d * 0.85 + volume_24h * 0.15 if avg_volume_7d > 0 else volume_24h
            await ctx.db.set(
                baseline_key,
                json.dumps({"avg_volume_7d": new_avg}),
                ex=7 * 86400,
            )

        # Calculate signals
        signals: list[dict] = []
        anomaly_score = 0.0

        # 1. Volume spike
        volume_ratio = _calc_volume_ratio(volume_24h, avg_volume_7d)
        if volume_ratio >= settings.pm_volume_spike_ratio:
            signals.append({
                "type": "Volume Spike",
                "description": f"{volume_ratio:.1f}x normal volume (${volume_24h:,.0f} vs avg ${avg_volume_7d:,.0f})",
                "score": min(volume_ratio / 10, 1.0),
            })
            anomaly_score += min(volume_ratio / 10, 1.0) * 0.4

        # 2. Orderbook imbalance
        imbalance = _calc_book_imbalance(orderbook)
        if imbalance >= settings.pm_book_imbalance_high or imbalance <= settings.pm_book_imbalance_low:
            direction = "bid-heavy" if imbalance > 0.5 else "ask-heavy"
            signals.append({
                "type": "Orderbook Imbalance",
                "description": f"{direction} ({imbalance:.2f} ratio)",
                "score": abs(imbalance - 0.5) * 2,
            })
            anomaly_score += abs(imbalance - 0.5) * 2 * 0.3

        # 3. Price velocity
        price_change_pct = await _calc_price_velocity(ctx, condition_id, price)
        if price_change_pct >= settings.pm_price_velocity_pct:
            signals.append({
                "type": "Price Velocity",
                "description": f"{price_change_pct:.1f}% change since last check",
                "score": min(price_change_pct / 20, 1.0),
            })
            anomaly_score += min(price_change_pct / 20, 1.0) * 0.3

        if not signals:
            continue

        # Dedup: check if alert already sent recently
        alert_dedup_key = f"pm:alert:{condition_id}:sent"
        if await ctx.db.exists(alert_dedup_key):
            continue

        # Inject signals and anomaly_score into event.data for delivery layer
        event.data["signals"] = signals
        event.data["anomaly_score"] = round(anomaly_score, 2)

        # AI analysis
        try:
            ai_result = await ctx.ai.analyze(
                "polymarket/anomaly_analysis.jinja2",
                {
                    "question": question,
                    "market_id": condition_id,
                    "current_price": price,
                    "volume_24h": volume_24h,
                    "avg_volume_7d": avg_volume_7d,
                    "signals": signals,
                    "anomaly_score": f"{anomaly_score:.2f}",
                    "end_date": event.data.get("end_date", ""),
                    "market_slug": event.data.get("market_slug", ""),
                    "event_slug": event.data.get("event_slug", ""),
                },
            )
        except Exception:
            logger.exception("AI analysis failed for market %s", condition_id)
            ai_result = {}

        ai_severity = ai_result.get("severity", "medium")
        severity_map = {"low": Severity.LOW, "medium": Severity.MEDIUM, "high": Severity.HIGH, "critical": Severity.CRITICAL}
        severity = severity_map.get(ai_severity, Severity.MEDIUM)

        enrichment = AIEnrichment(
            summary=ai_result.get("summary", ""),
            analysis=json.dumps(ai_result, ensure_ascii=False),
            confidence=float(ai_result.get("confidence", 0)),
        )

        alert = Alert(
            source=SourceType.POLYMARKET,
            rule_name="detect_polymarket_anomalies",
            severity=severity,
            title=f"Anomaly in: {question[:80]}",
            event=event,
            enrichment=enrichment,
        )

        # Store alert
        await ctx.db.lpush("alerts:polymarket", alert.model_dump_json())
        await ctx.db.ltrim("alerts:polymarket", 0, settings.alert_max_per_source - 1)

        # Set dedup key (24h TTL)
        await ctx.db.set(alert_dedup_key, "1", ex=86400)

        await ctx.delivery.send(alert)

        alerts_created += 1
        logger.info("Polymarket alert: %s (score=%.2f)", question[:60], anomaly_score)

    logger.info("Polymarket rule completed: %d alerts from %d events", alerts_created, len(events))
    return alerts_created > 0
