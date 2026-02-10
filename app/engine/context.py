from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.ai.client import AIClient
    from app.delivery.base import BaseDelivery
    from app.models import RuleConfig


@dataclass
class RuleContext:
    """Injected into every rule execution."""

    data: dict
    ai: AIClient
    db: Redis
    config: RuleConfig
    delivery: BaseDelivery
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("rule"))
