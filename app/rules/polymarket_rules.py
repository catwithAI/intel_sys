from __future__ import annotations

import json
import logging

from app.config import settings
from app.corroboration.service import CorroborationService
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import AIEnrichment, Alert, Event, Severity, SourceType
from app.sources.polymarket import PolymarketSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier 2 helpers (deep CLOB analysis — unchanged logic)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tier 1: Lightweight screening using Gamma fields only
# ---------------------------------------------------------------------------

def _tier1_screen(events: list[Event]) -> list[tuple[Event, dict]]:
    """Screen all markets using Gamma-provided fields. Zero CLOB calls.

    Returns list of (event, wide_info) tuples sorted by breaking_score desc,
    capped at pm_wide_max_tier2.
    wide_info contains: wide_signals, breaking_score.
    """
    candidates: list[tuple[Event, dict]] = []

    for event in events:
        volume_24h = float(event.data.get("volume_24h", 0))

        # Volume floor filter — skip ultra-low-volume noise
        if volume_24h < settings.pm_wide_volume_floor:
            continue

        volume_1wk = float(event.data.get("volume_1wk", 0))
        one_day_change = float(event.data.get("one_day_price_change", 0))
        one_hour_change = float(event.data.get("one_hour_price_change", 0))
        spread = float(event.data.get("spread", 0))

        wide_signals: list[dict] = []

        # 1. Volume Spike: 24h vs daily average (volume_1wk / 7)
        daily_avg = volume_1wk / 7.0 if volume_1wk > 0 else 0.0
        vol_ratio = volume_24h / daily_avg if daily_avg > 0 else 0.0
        vol_component = 0.0
        if vol_ratio >= settings.pm_wide_volume_spike_ratio:
            vol_component = min(vol_ratio / 10.0, 1.0)
            wide_signals.append({
                "type": "Wide: Volume Spike",
                "description": f"{vol_ratio:.1f}x daily avg (${volume_24h:,.0f} vs ${daily_avg:,.0f}/day)",
                "score": vol_component,
            })

        # 2. Price Velocity 1d
        price_component = 0.0
        if abs(one_day_change) >= settings.pm_wide_price_velocity_1d:
            score_1d = min(abs(one_day_change) / 0.20, 1.0)
            price_component = max(price_component, score_1d)
            wide_signals.append({
                "type": "Wide: Price Velocity 1d",
                "description": f"{one_day_change:+.1%} in 24h",
                "score": score_1d,
            })

        # 3. Price Velocity 1h
        if abs(one_hour_change) >= settings.pm_wide_price_velocity_1h:
            score_1h = min(abs(one_hour_change) / 0.10, 1.0)
            price_component = max(price_component, score_1h)
            wide_signals.append({
                "type": "Wide: Price Velocity 1h",
                "description": f"{one_hour_change:+.1%} in 1h",
                "score": score_1h,
            })

        # 4. Spread Anomaly
        spread_component = 0.0
        if spread >= settings.pm_wide_spread_threshold:
            spread_component = min(spread / 0.30, 1.0)
            wide_signals.append({
                "type": "Wide: Spread Anomaly",
                "description": f"spread {spread:.2f} (threshold {settings.pm_wide_spread_threshold})",
                "score": spread_component,
            })

        if not wide_signals:
            continue

        # Composite breaking score
        breaking_score = (
            vol_component * 0.4
            + price_component * 0.3
            + spread_component * 0.3
        )

        if breaking_score < settings.pm_wide_breaking_threshold:
            continue

        candidates.append((event, {
            "wide_signals": wide_signals,
            "breaking_score": round(breaking_score, 3),
        }))

    # Sort by breaking_score desc, cap at max_tier2
    candidates.sort(key=lambda x: x[1]["breaking_score"], reverse=True)
    candidates = candidates[:settings.pm_wide_max_tier2]

    logger.info(
        "Tier 1 screen: %d / %d markets passed (breaking ≥ %.2f)",
        len(candidates), len(events), settings.pm_wide_breaking_threshold,
    )
    return candidates


def _build_market_dicts(candidates: list[tuple[Event, dict]]) -> list[dict]:
    """Reconstruct market dicts from Tier 1 Events for fetch_selected()."""
    market_dicts: list[dict] = []
    for event, _ in candidates:
        d = event.data
        market_dicts.append({
            "conditionId": d.get("condition_id", ""),
            "id": event.metadata.get("market_id", ""),
            "question": d.get("question", ""),
            "clobTokenIds": d.get("clob_token_ids", []),
            "slug": d.get("market_slug", ""),
            "_event_slug": d.get("event_slug", ""),
            "endDate": d.get("end_date", ""),
            "outcomes": d.get("outcomes", []),
            "volume24hr": str(d.get("volume_24h", 0)),
            "volume": str(d.get("volume_total", 0)),
            "category": event.metadata.get("category", ""),
        })
    return market_dicts


# ---------------------------------------------------------------------------
# Tier 2: Deep CLOB + Redis + AI analysis
# ---------------------------------------------------------------------------

async def _tier2_analyze(
    ctx: RuleContext,
    deep_events: list[Event],
    wide_info_map: dict[str, dict],
) -> int:
    """Run full deep analysis on Tier 2 events. Returns number of alerts created."""
    alerts_created = 0

    for event in deep_events:
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

        # Calculate deep signals
        deep_signals: list[dict] = []
        anomaly_score = 0.0

        # 1. Volume spike (deep — Redis baseline)
        volume_ratio = _calc_volume_ratio(volume_24h, avg_volume_7d)
        if volume_ratio >= settings.pm_volume_spike_ratio:
            deep_signals.append({
                "type": "Deep: Volume Spike",
                "description": f"{volume_ratio:.1f}x normal volume (${volume_24h:,.0f} vs avg ${avg_volume_7d:,.0f})",
                "score": min(volume_ratio / 10, 1.0),
            })
            anomaly_score += min(volume_ratio / 10, 1.0) * 0.4

        # 2. Orderbook imbalance
        imbalance = _calc_book_imbalance(orderbook)
        if imbalance >= settings.pm_book_imbalance_high or imbalance <= settings.pm_book_imbalance_low:
            direction = "bid-heavy" if imbalance > 0.5 else "ask-heavy"
            deep_signals.append({
                "type": "Deep: Orderbook Imbalance",
                "description": f"{direction} ({imbalance:.2f} ratio)",
                "score": abs(imbalance - 0.5) * 2,
            })
            anomaly_score += abs(imbalance - 0.5) * 2 * 0.3

        # 3. Price velocity (deep — Redis snapshot)
        price_change_pct = await _calc_price_velocity(ctx, condition_id, price)
        if price_change_pct >= settings.pm_price_velocity_pct:
            deep_signals.append({
                "type": "Deep: Price Velocity",
                "description": f"{price_change_pct:.1f}% change since last check",
                "score": min(price_change_pct / 20, 1.0),
            })
            anomaly_score += min(price_change_pct / 20, 1.0) * 0.3

        # Merge Tier 1 wide signals
        wide_info = wide_info_map.get(condition_id, {})
        wide_signals = wide_info.get("wide_signals", [])
        breaking_score = wide_info.get("breaking_score", 0)

        # Combine: wide signals always present (they triggered Tier 2),
        # deep signals may or may not fire on first run (cold start)
        all_signals = wide_signals + deep_signals

        if not all_signals:
            continue

        # Dedup: check if alert already sent recently
        alert_dedup_key = f"pm:alert:{condition_id}:sent"
        if await ctx.db.exists(alert_dedup_key):
            continue

        # Inject signals into event.data for delivery layer
        event.data["signals"] = all_signals
        event.data["anomaly_score"] = round(anomaly_score, 2)
        event.data["breaking_score"] = breaking_score
        # Carry outcome_prices from Tier 1 (Gamma) into deep event for delivery
        outcome_prices = wide_info.get("outcome_prices", [])
        event.data["outcome_prices"] = outcome_prices

        # Build outcomes with prices for AI context
        outcomes = event.data.get("outcomes", [])
        outcomes_with_prices = []
        for i, outcome in enumerate(outcomes):
            p = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
            outcomes_with_prices.append({"outcome": outcome, "price": p})

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
                    "signals": all_signals,
                    "anomaly_score": f"{anomaly_score:.2f}",
                    "breaking_score": f"{breaking_score:.2f}",
                    "end_date": event.data.get("end_date", ""),
                    "market_slug": event.data.get("market_slug", ""),
                    "event_slug": event.data.get("event_slug", ""),
                    "outcomes_with_prices": outcomes_with_prices,
                },
            )
        except Exception:
            logger.exception("AI analysis failed for market %s", condition_id)
            ai_result = {}

        # Skip alert if AI analysis failed or returned no useful content
        if not ai_result or not ai_result.get("summary"):
            logger.warning("Skipping market %s: AI returned empty result", condition_id)
            continue

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

        # Corroboration: search HN only (skip Twitter to avoid 429 rate limits)
        corroboration_svc = CorroborationService()
        try:
            corr = await corroboration_svc.search(alert, skip_twitter=True)
            if corr:
                alert.corroboration = corr.to_dict()
                new_conf = min(max(alert.enrichment.confidence + corr.confidence_boost, 0.0), 1.0)
                alert.enrichment.confidence = new_conf
                if corr.confidence_boost >= 0.15 and alert.severity == Severity.MEDIUM:
                    alert.severity = Severity.HIGH
        finally:
            await corroboration_svc.close()

        # Store alert
        alert_json = alert.model_dump_json()
        await ctx.db.lpush("alerts:polymarket", alert_json)
        await ctx.db.ltrim("alerts:polymarket", 0, settings.alert_max_per_source - 1)

        # Publish to Redis Stream for poly_trader consumption
        await ctx.db.xadd(
            settings.signal_stream_key,
            {"alert": alert_json},
            maxlen=1000,
        )

        # Set dedup key (24h TTL)
        await ctx.db.set(alert_dedup_key, "1", ex=86400)

        # Route: only geopolitical alerts → immediate push
        #        everything else → 6-hourly digest buffer
        ai_data_for_routing: dict = {}
        try:
            ai_data_for_routing = json.loads(alert.enrichment.analysis) if alert.enrichment.analysis else {}
        except (json.JSONDecodeError, TypeError):
            pass

        geo_impact = ai_data_for_routing.get("geopolitical_impact", "").strip()
        # Prompt instructs AI to return "" when no geopolitical relevance.
        # Fallback: filter out residual "no impact" statements.
        _no_impact_markers = (
            "无影响", "无实质", "不涉及", "无关", "没有影响", "影响较小", "影响有限",
            "no impact", "not relevant", "no direct", "limited impact",
        )
        has_geo_impact = bool(geo_impact) and not any(m in geo_impact for m in _no_impact_markers)
        is_urgent = has_geo_impact

        if is_urgent:
            await ctx.delivery.send(alert)
        else:
            # Buffer for hourly digest
            await ctx.db.lpush("pm:alerts:hourly_buffer", alert.model_dump_json())

        alerts_created += 1
        logger.info(
            "Polymarket alert: %s (breaking=%.2f, anomaly=%.2f)",
            question[:60], breaking_score, anomaly_score,
        )

    return alerts_created


# ---------------------------------------------------------------------------
# Main rule: Two-tier funnel
# ---------------------------------------------------------------------------

@rule_registry.register(
    source="polymarket",
    schedule="interval:90s",
    trigger="threshold",
)
async def detect_polymarket_anomalies(ctx: RuleContext) -> bool:
    """Two-tier funnel: wide Gamma scan → CLOB deep analysis for anomalies."""
    source = PolymarketSource()

    try:
        # ---- Tier 1: Wide scan (zero CLOB calls) ----
        wide_events = await source.fetch_wide()

        if not wide_events:
            logger.info("No Polymarket markets fetched from Gamma")
            return False

        candidates = _tier1_screen(wide_events)

        if not candidates:
            logger.info("Tier 1: no candidates passed screening")
            return False

        # Build condition_id → wide_info map for merging into Tier 2
        wide_info_map: dict[str, dict] = {}
        for event, wide_info in candidates:
            cid = event.data.get("condition_id", "")
            # Carry Gamma fields (outcome_prices) into wide_info for Tier 2
            wide_info["outcome_prices"] = event.data.get("outcome_prices", [])
            wide_info_map[cid] = wide_info

        # ---- Tier 2: Deep CLOB analysis for candidates only ----
        market_dicts = _build_market_dicts(candidates)
        deep_events = await source.fetch_selected(market_dicts)

        if not deep_events:
            logger.info("Tier 2: no CLOB data returned for candidates")
            return False

        alerts_created = await _tier2_analyze(ctx, deep_events, wide_info_map)

    finally:
        await source.stop()

    logger.info(
        "Polymarket two-tier funnel: %d wide → %d candidates → %d deep → %d alerts",
        len(wide_events), len(candidates), len(deep_events), alerts_created,
    )
    return alerts_created > 0
