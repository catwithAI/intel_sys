from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.defense.collectors.rss import RSSCollector
from app.defense.converter import to_event
from app.defense.deduper import Deduper
from app.defense.health import SourceHealthManager
from app.defense.normalizer import normalize
from app.defense.rate_limiter import DomainRateLimiter
from app.defense.scorer import Scorer
from app.defense.source_loader import SourceLoader
from app.defense.storage import DefenseStorage
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.memory.pool import EventMemoryPool
from app.models import Alert, Severity, SourceType

logger = logging.getLogger(__name__)


def _score_to_severity(score: float) -> Severity:
    if score >= 0.7:
        return Severity.HIGH
    elif score >= 0.5:
        return Severity.MEDIUM
    else:
        return Severity.LOW


@rule_registry.register(
    source="defense",
    schedule=f"interval:{settings.defense_rss_interval}s",
    trigger="batch",
)
async def ingest_defense_news(ctx: RuleContext) -> bool:
    """Defense news ingestion pipeline: RSS → normalize → dedup → score → pool/alert."""
    started_at = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:12]
    stats: dict = {}

    # Dependency injection via app_state
    pg_pool = getattr(ctx.app_state, "pg_pool", None) if ctx.app_state else None
    defense_delivery = getattr(ctx.app_state, "defense_delivery", None) if ctx.app_state else None
    storage = DefenseStorage(pg_pool) if pg_pool else None
    health_mgr = SourceHealthManager(
        storage,
        cooldown_hours=settings.defense_cooldown_hours,
        cooling_threshold=settings.defense_max_consecutive_failures,
        disable_threshold=settings.defense_disable_threshold,
    ) if storage else None

    # 0. Load source configs (no cache, re-read each run)
    specs = SourceLoader.load_defense_sources("sources/")
    specs_map = {s.id: s for s in specs}

    # 1. Refresh health + filter unavailable sources
    if health_mgr:
        await health_mgr.refresh_cache()
        await health_mgr.flush_recovery()
    active_specs = [s for s in specs if not health_mgr or health_mgr.is_available(s.id)]
    # Flush again after is_available() to write back any lazy recoveries
    if health_mgr:
        await health_mgr.flush_recovery()
    stats["sources_total"] = len(specs)
    stats["sources_skipped"] = len(specs) - len(active_specs)

    # 2. Concurrent collection
    http_client = httpx.AsyncClient(timeout=settings.defense_rss_timeout)
    rate_limiter = DomainRateLimiter()
    collector = RSSCollector(
        http_client, rate_limiter,
        min_interval=settings.defense_domain_min_interval,
        redis=ctx.db,
    )
    sem = asyncio.Semaphore(settings.defense_rss_concurrency)

    async def _fetch(spec):
        async with sem:
            return await collector.collect(spec)

    try:
        results = await asyncio.gather(
            *[_fetch(s) for s in active_specs],
            return_exceptions=True,
        )
    finally:
        await http_client.aclose()

    # 3. Process results + update health
    raw_events = []
    ok_count = err_count = not_modified_count = skipped_count = 0
    for spec, result in zip(active_specs, results):
        if isinstance(result, Exception):
            err_count += 1
            if health_mgr:
                await health_mgr.record_failure(spec.id, str(result))
        elif result.status == "error":
            err_count += 1
            if health_mgr:
                await health_mgr.record_failure(spec.id, result.error or "unknown")
        elif result.status == "skipped":
            skipped_count += 1
        elif result.status == "not_modified":
            not_modified_count += 1
            if health_mgr:
                await health_mgr.record_success(spec.id)
        else:
            ok_count += 1
            raw_events.extend(result.events)
            if health_mgr:
                await health_mgr.record_success(spec.id)

    stats["sources_ok"] = ok_count
    stats["sources_error"] = err_count
    stats["sources_not_modified"] = not_modified_count
    stats["sources_skipped_neg_cache"] = skipped_count
    stats["raw_events"] = len(raw_events)

    # 4. Normalize
    normalized = []
    for re in raw_events:
        spec = specs_map.get(re.site_id)
        if spec:
            normalized.append(normalize(spec, re))
    stats["normalized_events"] = len(normalized)

    # 5. PG insert (before dedup, append-only)
    if storage:
        inserted = await storage.insert_normalized_events(normalized, run_id)
        stats["pg_inserted"] = inserted

    # 6. Dedup
    deduper = Deduper(ctx.db, settings.defense_dedup_ttl)
    unique = await deduper.filter_duplicates(normalized)
    stats["after_dedup"] = len(unique)

    # 7. Filter + Score
    scorer = Scorer()
    filtered = scorer.stage1_filter(unique, specs_map)
    stats["after_stage1"] = len(filtered)
    scored = scorer.stage2_score(filtered, specs_map)
    top_events = scorer.topk(scored, settings.defense_topk)
    stats["after_stage2_topk"] = len(top_events)

    # 8. Convert → Event → Memory Pool (all events)
    events = [to_event(ne) for ne in top_events]
    pool = EventMemoryPool(ctx.db, ctx.ai)
    added = await pool.add_events_batch(events)
    stats["events_to_pool"] = added

    # 9. High-score events → Alert → Feishu
    alert_threshold = settings.defense_alert_threshold
    alerts = []
    for ne, ev in zip(top_events, events):
        if ne.pre_score >= alert_threshold:
            alert = Alert(
                source=SourceType.DEFENSE,
                rule_name="ingest_defense_news",
                severity=_score_to_severity(ne.pre_score),
                title=f"[DEFENSE] {ne.title}",
                event=ev,
            )
            alerts.append(alert)
            alert_json = alert.model_dump_json()
            await ctx.db.lpush(f"alerts:{SourceType.DEFENSE.value}", alert_json)
            await ctx.db.ltrim(f"alerts:{SourceType.DEFENSE.value}", 0, settings.alert_max_per_source - 1)

    stats["alerts_generated"] = len(alerts)

    # Push to defense Feishu bot
    if alerts and defense_delivery:
        await defense_delivery.send_batch(alerts)
    stats["alerts_pushed"] = len(alerts) if defense_delivery else 0

    # 10. Run history
    finished_at = datetime.now(timezone.utc)
    stats["duration_ms"] = (finished_at - started_at).total_seconds() * 1000

    if err_count == 0:
        run_status = "ok"
    elif ok_count > 0 or not_modified_count > 0:
        run_status = "partial"
    else:
        run_status = "error"

    if storage:
        await storage.insert_run(run_id, "ingest_defense_news", started_at, finished_at, run_status, stats)

    ctx.logger.info("defense run completed", extra={"run_id": run_id, **stats})
    return len(alerts) > 0
