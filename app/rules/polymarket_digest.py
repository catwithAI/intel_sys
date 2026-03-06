from __future__ import annotations

import logging

from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import Alert

logger = logging.getLogger(__name__)


@rule_registry.register(
    source="polymarket",
    schedule="cron:0 */6 * * *",  # every 6 hours (00:00, 06:00, 12:00, 18:00)
    trigger="batch",
)
async def send_polymarket_digest(ctx: RuleContext) -> bool:
    """Flush 6-hourly Polymarket buffer into a single digest card."""
    buffer_key = "pm:alerts:hourly_buffer"
    raw_alerts = await ctx.db.lrange(buffer_key, 0, -1)
    if not raw_alerts:
        logger.info("Polymarket digest buffer is empty — nothing to send")
        return False

    alerts: list[Alert] = []
    for raw in raw_alerts:
        try:
            alerts.append(Alert.model_validate_json(raw))
        except Exception:
            logger.warning("Failed to parse buffered alert, skipping")
            continue

    if not alerts:
        await ctx.db.delete(buffer_key)
        return False

    await ctx.delivery.send_batch(alerts)

    # Clear buffer after successful send
    await ctx.db.delete(buffer_key)

    logger.info("Polymarket 6-hourly digest sent: %d alerts", len(alerts))
    return True
