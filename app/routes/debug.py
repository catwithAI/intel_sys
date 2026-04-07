from __future__ import annotations

import asyncio
import logging
import time
from functools import partial

from fastapi import APIRouter, HTTPException, Request

from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import RuleConfig, SourceType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debug"])


@router.get("/debug/rules")
async def list_rules():
    """List all registered rules and their metadata."""
    rules = []
    for name, meta in rule_registry.rules.items():
        rules.append({
            "name": meta.name,
            "source": meta.source,
            "schedule": meta.schedule,
            "trigger": meta.trigger,
        })
    return {"rules": rules, "count": len(rules)}


@router.get("/debug/events/{source}")
async def debug_events(source: str, request: Request):
    """Return the most recent events/alerts for debugging."""
    redis = request.app.state.redis
    key = f"alerts:{source}"
    raw = await redis.lrange(key, 0, 4)
    import json
    alerts = [json.loads(a) for a in raw]
    return {"source": source, "recent_alerts": alerts, "count": len(alerts)}


@router.get("/debug/state/{key:path}")
async def debug_state(key: str, request: Request):
    """Query a Redis key for debugging. Supports string, list, set, zset, hash."""
    import json as _json

    redis = request.app.state.redis
    key_type = await redis.type(key)

    if key_type == "none":
        return {"key": key, "type": "none", "value": None, "exists": False}

    if key_type == "string":
        val = await redis.get(key)
        try:
            parsed = _json.loads(val)
            return {"key": key, "type": "string", "value": parsed, "exists": True}
        except (_json.JSONDecodeError, TypeError):
            return {"key": key, "type": "string", "value": val, "exists": True}

    if key_type == "list":
        val = await redis.lrange(key, 0, 9)
        return {"key": key, "type": "list", "length": await redis.llen(key), "value": val[:10], "exists": True}

    if key_type == "zset":
        count = await redis.zcard(key)
        # Return last 10 (most recent by score)
        val = await redis.zrevrange(key, 0, 9, withscores=True)
        items = [{"member": m, "score": s} for m, s in val]
        return {"key": key, "type": "zset", "count": count, "value": items, "exists": True}

    if key_type == "set":
        count = await redis.scard(key)
        val = await redis.srandmember(key, 10)
        return {"key": key, "type": "set", "count": count, "value": val, "exists": True}

    if key_type == "hash":
        val = await redis.hgetall(key)
        return {"key": key, "type": "hash", "value": val, "exists": True}

    return {"key": key, "type": key_type, "value": None, "exists": True}


@router.get("/debug/scheduler")
async def debug_scheduler(request: Request):
    """List scheduled jobs."""
    scheduler = request.app.state.scheduler
    jobs = []
    for job in scheduler.jobs:
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "paused": job.next_run_time is None,
        })
    return {"jobs": jobs, "count": len(jobs)}


@router.post("/debug/scheduler/pause/{rule_name}")
async def pause_rule(rule_name: str, request: Request):
    """Pause a scheduled rule. Stops automatic execution."""
    scheduler = request.app.state.scheduler
    job_id = f"rule:{rule_name}"
    if scheduler.pause_job(job_id):
        return {"status": "paused", "rule": rule_name}
    raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")


@router.post("/debug/scheduler/resume/{rule_name}")
async def resume_rule(rule_name: str, request: Request):
    """Resume a paused scheduled rule."""
    scheduler = request.app.state.scheduler
    job_id = f"rule:{rule_name}"
    if scheduler.resume_job(job_id):
        return {"status": "resumed", "rule": rule_name}
    raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")


@router.post("/debug/scheduler/pause-source/{source}")
async def pause_source(source: str, request: Request):
    """Pause all scheduled rules for a source."""
    scheduler = request.app.state.scheduler
    paused = []
    for name, meta in rule_registry.rules.items():
        if meta.source == source:
            job_id = f"rule:{name}"
            if scheduler.pause_job(job_id):
                paused.append(name)
    if not paused:
        raise HTTPException(status_code=404, detail=f"No rules found for source '{source}'")
    return {"status": "paused", "source": source, "rules": paused}


@router.post("/debug/scheduler/resume-source/{source}")
async def resume_source(source: str, request: Request):
    """Resume all scheduled rules for a source."""
    scheduler = request.app.state.scheduler
    resumed = []
    for name, meta in rule_registry.rules.items():
        if meta.source == source:
            job_id = f"rule:{name}"
            if scheduler.resume_job(job_id):
                resumed.append(name)
    if not resumed:
        raise HTTPException(status_code=404, detail=f"No rules found for source '{source}'")
    return {"status": "resumed", "source": source, "rules": resumed}


@router.post("/debug/trigger/{rule_name}")
async def trigger_rule(rule_name: str, request: Request):
    """Manually trigger a single rule by name. Use for debugging."""
    meta = rule_registry.rules.get(rule_name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_name}' not found")

    redis = request.app.state.redis
    ai_client = request.app.state.ai_client
    delivery = request.app.state.delivery
    app_state = getattr(request.app.state, "defense_app_state", None)

    ctx = RuleContext(
        data={},
        ai=ai_client,
        db=redis,
        config=RuleConfig(name=meta.name, source=SourceType(meta.source)),
        delivery=delivery,
        logger=logging.getLogger(f"rule.{rule_name}"),
        app_state=app_state,
    )

    t0 = time.time()
    try:
        result = await meta.fn(ctx)
        elapsed = round(time.time() - t0, 2)
        return {
            "rule": rule_name,
            "source": meta.source,
            "result": result,
            "elapsed_seconds": elapsed,
        }
    except Exception as exc:
        elapsed = round(time.time() - t0, 2)
        logger.exception("Manual trigger of %s failed", rule_name)
        raise HTTPException(
            status_code=500,
            detail=f"Rule '{rule_name}' failed after {elapsed}s: {exc}",
        )


@router.post("/debug/trigger-source/{source}")
async def trigger_source(source: str, request: Request):
    """Manually trigger all rules for a given source. Use for debugging."""
    matching = {n: m for n, m in rule_registry.rules.items() if m.source == source}
    if not matching:
        raise HTTPException(status_code=404, detail=f"No rules found for source '{source}'")

    redis = request.app.state.redis
    ai_client = request.app.state.ai_client
    delivery = request.app.state.delivery
    app_state = getattr(request.app.state, "defense_app_state", None)
    results = {}

    for name, meta in matching.items():
        ctx = RuleContext(
            data={},
            ai=ai_client,
            db=redis,
            config=RuleConfig(name=meta.name, source=SourceType(meta.source)),
            delivery=delivery,
            logger=logging.getLogger(f"rule.{name}"),
            app_state=app_state,
        )
        t0 = time.time()
        try:
            result = await meta.fn(ctx)
            results[name] = {"result": result, "elapsed_seconds": round(time.time() - t0, 2)}
        except Exception as exc:
            results[name] = {"error": str(exc), "elapsed_seconds": round(time.time() - t0, 2)}
            logger.exception("Manual trigger of %s failed", name)

    return {"source": source, "rules_triggered": len(results), "results": results}


@router.post("/system/reload")
async def reload_rules(request: Request):
    """Reload all rules from the rules package."""
    from app.main import execute_rule

    rule_registry.reload_rules()

    # Re-register scheduler jobs so changed schedules take effect
    scheduler = request.app.state.scheduler
    redis_client = request.app.state.redis
    ai_client = request.app.state.ai_client
    delivery = request.app.state.delivery
    app_state = getattr(request.app.state, "defense_app_state", None)

    for name, meta in rule_registry.rules.items():
        job_fn = partial(execute_rule, name, redis_client, ai_client, delivery, app_state)
        scheduler.register_rule(meta, job_fn)

    return {
        "status": "reloaded",
        "rules": list(rule_registry.rules.keys()),
        "count": len(rule_registry.rules),
    }


@router.get("/debug/defense/health")
async def defense_health(request: Request):
    """Get defense source health status."""
    pg_pool = getattr(request.app.state, "pg_pool", None)
    if not pg_pool:
        return {"error": "PostgreSQL not configured", "records": []}
    from app.defense.storage import DefenseStorage
    storage = DefenseStorage(pg_pool)
    records = await storage.get_source_health()
    return {"records": records}


@router.get("/debug/defense/runs")
async def defense_runs(request: Request):
    """Get recent defense run history."""
    pg_pool = getattr(request.app.state, "pg_pool", None)
    if not pg_pool:
        return {"error": "PostgreSQL not configured", "runs": []}
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM run_history ORDER BY created_at DESC LIMIT 20"
        )
    return {"runs": [dict(r) for r in rows]}
