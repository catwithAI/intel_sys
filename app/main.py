from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import partial

import redis.asyncio as aioredis
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ai.client import AIClient
from app.config import settings
from app.delivery.feishu import FeishuWebhookDelivery, NoopDelivery
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.engine.scheduler import Scheduler
from app.models import RuleConfig, SourceType
from app.routes.alerts import router as alerts_router
from app.routes.debug import router as debug_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def execute_rule(
    rule_name: str,
    redis_client: aioredis.Redis,
    ai_client: AIClient,
    delivery,
) -> None:
    """Wrapper that constructs RuleContext and executes a rule."""
    meta = rule_registry.rules.get(rule_name)
    if not meta:
        logger.error("Rule not found: %s", rule_name)
        return

    config = RuleConfig(
        name=meta.name,
        source=SourceType(meta.source),
    )
    ctx = RuleContext(
        data={},
        ai=ai_client,
        db=redis_client,
        config=config,
        delivery=delivery,
        logger=logging.getLogger(f"rule.{rule_name}"),
    )

    try:
        logger.info("Executing rule: %s", rule_name)
        result = await meta.fn(ctx)
        logger.info("Rule %s completed: result=%s", rule_name, result)
    except Exception:
        logger.exception("Rule %s failed", rule_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of all components."""
    # --- Startup ---
    logger.info("Starting Intel System...")

    # Redis
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Redis connected: %s", settings.redis_url)
    except Exception:
        logger.warning("Redis connection failed — running without persistence")
        # Create a fake redis for development (in-memory fallback)
        redis_client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)

    app.state.redis = redis_client

    # AI Client
    ai_client = AIClient()
    app.state.ai_client = ai_client

    # Delivery
    if settings.feishu_webhook_url:
        delivery = FeishuWebhookDelivery(
            settings.feishu_webhook_url, settings.feishu_webhook_secret
        )
        logger.info("Feishu delivery enabled")
    else:
        delivery = NoopDelivery()
        logger.info("Feishu delivery disabled (no webhook URL)")
    app.state.delivery = delivery

    # Load rules
    rule_registry.clear()
    rule_registry.load_rules_from_package("app.rules")
    logger.info("Loaded %d rules", len(rule_registry.rules))

    # Scheduler
    scheduler = Scheduler()
    app.state.scheduler = scheduler

    for name, meta in rule_registry.rules.items():
        job_fn = partial(execute_rule, name, redis_client, ai_client, delivery)
        scheduler.register_rule(meta, job_fn)

    scheduler.start()

    yield

    # --- Shutdown ---
    logger.info("Shutting down Intel System...")
    scheduler.shutdown()
    await delivery.close()
    await ai_client.close()
    await redis_client.aclose()


app = FastAPI(
    title="Intel System",
    description="Event-Driven Intelligence & Decision System",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(alerts_router)
app.include_router(debug_router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/")
async def root():
    return {
        "name": "Intel System",
        "version": "0.1.0",
        "status": "running",
        "rules_loaded": len(rule_registry.rules),
    }
