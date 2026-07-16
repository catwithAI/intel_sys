from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.config import settings
from app.defense.source_loader import SourceLoader
from app.engine.registry import rule_registry

router = APIRouter(prefix="/dashboard-api", tags=["dashboard"])

SOURCE_META: dict[str, dict[str, str]] = {
    "correlation": {"label": "聚类引擎", "family": "reasoning", "region": "GLOBAL"},
    "defense": {"label": "防务信源", "family": "news", "region": "GLOBAL"},
    "cls": {"label": "财联社", "family": "news", "region": "CN"},
    "xueqiu": {"label": "雪球", "family": "community", "region": "CN"},
    "reddit": {"label": "Reddit", "family": "community", "region": "GLOBAL"},
    "github": {"label": "GitHub", "family": "technology", "region": "GLOBAL"},
    "hackernews": {"label": "Hacker News", "family": "technology", "region": "GLOBAL"},
    "polymarket": {"label": "Polymarket", "family": "market", "region": "GLOBAL"},
}


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_object(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _cluster_from_alert(alert: dict[str, Any], rank: int) -> dict[str, Any]:
    event = alert.get("event") or {}
    data = event.get("data") or {}
    enrichment = alert.get("enrichment") or {}
    chain = data.get("chain") or []

    source_names: list[str] = []
    for item in chain:
        if isinstance(item, dict):
            candidate = item.get("source") or item.get("name") or item.get("title")
        else:
            candidate = item
        if candidate:
            source_names.append(str(candidate))

    if not source_names:
        source_names = [SOURCE_META.get(alert.get("source", ""), {}).get("label", alert.get("source", "未知"))]

    confidence = float(enrichment.get("confidence") or data.get("confidence") or 0.5)
    return {
        "id": alert.get("id") or f"cluster-{rank}",
        "title": alert.get("title") or data.get("title") or "未命名情报聚类",
        "summary": enrichment.get("summary") or data.get("reasoning") or "等待分析摘要",
        "category": data.get("category") or "跨域情报",
        "confidence": round(max(0.0, min(confidence, 1.0)), 2),
        "severity": alert.get("severity") or ("high" if confidence >= 0.7 else "medium"),
        "created_at": alert.get("created_at") or event.get("timestamp"),
        "sources": source_names[:5],
        "signal_count": max(len(chain), int(data.get("merged_count") or 1)),
        "direction": data.get("investment_direction") or data.get("timeframe") or "持续观察",
        "url": data.get("url") or "",
    }


def _fallback_cluster(alert: dict[str, Any], rank: int) -> dict[str, Any]:
    cluster = _cluster_from_alert(alert, rank)
    source = alert.get("source", "unknown")
    cluster.update({
        "category": SOURCE_META.get(source, {}).get("family", "实时信号"),
        "sources": [SOURCE_META.get(source, {}).get("label", source)],
        "signal_count": 1,
    })
    return cluster


def _parse_schedule(schedule: str) -> tuple[int, str]:
    """Return a 0-100 collection intensity and a short human label."""
    if schedule.startswith("interval:"):
        raw = schedule.split(":", 1)[1]
        unit = raw[-1]
        try:
            amount = float(raw[:-1])
        except ValueError:
            return 50, raw
        seconds = amount * {"s": 1, "m": 60, "h": 3600}.get(unit, 1)
        score = 100 - 15 * math.log10(max(seconds, 30) / 30)
        label = f"每 {raw}"
        return round(max(28, min(100, score))), label
    if schedule.startswith("cron:"):
        cron = schedule.split(":", 1)[1]
        return 38, f"定时 · {cron}"
    return 45, schedule or "按需"


async def _safe_lrange(redis: Any, key: str, start: int, end: int) -> list[Any]:
    try:
        return await redis.lrange(key, start, end)
    except Exception:
        return []


async def _safe_zcard(redis: Any, key: str) -> int:
    try:
        return int(await redis.zcard(key))
    except Exception:
        return 0


@router.get("/overview")
async def dashboard_overview(request: Request):
    redis = request.app.state.redis
    correlation_raw = await _safe_lrange(redis, "alerts:correlation", 0, 11)
    correlation_alerts = [value for raw in correlation_raw if (value := _json_object(raw))]
    clusters = [_cluster_from_alert(alert, idx) for idx, alert in enumerate(correlation_alerts)]

    # A new installation may not have run the daily correlation rule yet. Surface
    # recent source alerts so the board is still useful, while marking its mode.
    mode = "clustered"
    if not clusters:
        mode = "signals"
        recent: list[dict[str, Any]] = []
        for source in SOURCE_META:
            if source == "correlation":
                continue
            raws = await _safe_lrange(redis, f"alerts:{source}", 0, 2)
            recent.extend(value for raw in raws if (value := _json_object(raw)))
        recent.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        clusters = [_fallback_cluster(alert, idx) for idx, alert in enumerate(recent[:12])]

    memory_count = await _safe_zcard(redis, settings.memory_pool_key)
    active_rules = len(rule_registry.rules)
    connected_sources = len({meta.source for meta in rule_registry.rules.values()})
    high_priority = sum(1 for item in clusters if item["severity"] in ("high", "critical"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "clusters": clusters,
        "metrics": {
            "memory_events": memory_count,
            "active_rules": active_rules,
            "connected_sources": connected_sources,
            "high_priority": high_priority,
        },
    }


@router.get("/sources")
async def dashboard_sources(request: Request):
    scheduler = request.app.state.scheduler
    jobs_by_name = {job.id.removeprefix("rule:"): job for job in scheduler.jobs}
    rows: list[dict[str, Any]] = []

    # Runtime sources derive their collection pressure from the real schedules.
    for name, meta in rule_registry.rules.items():
        intensity, cadence = _parse_schedule(meta.schedule)
        job = jobs_by_name.get(name)
        info = SOURCE_META.get(meta.source, {"label": meta.source, "family": "other", "region": "GLOBAL"})
        rows.append({
            "id": name,
            "source": meta.source,
            "name": info["label"],
            "family": info["family"],
            "region": info["region"],
            "collector": "runtime",
            "status": "paused" if job and job.next_run_time is None else "ok",
            "intensity": intensity,
            "cadence": cadence,
            "credibility": None,
            "next_run": _as_iso(job.next_run_time) if job else None,
            "last_success_at": None,
            "total_fetches": None,
            "total_failures": None,
        })

    # Defense sources have source-level quality and health records.
    source_dir = Path(__file__).resolve().parents[2] / "sources"
    defense_specs = SourceLoader.load_defense_sources(str(source_dir))
    health_by_id: dict[str, dict[str, Any]] = {}
    pg_pool = getattr(request.app.state, "pg_pool", None)
    if pg_pool:
        try:
            from app.defense.storage import DefenseStorage

            records = await DefenseStorage(pg_pool).get_source_health()
            health_by_id = {record["site_id"]: record for record in records}
        except Exception:
            health_by_id = {}

    defense_intensity, defense_cadence = _parse_schedule(f"interval:{settings.defense_rss_interval}s")
    for spec in defense_specs:
        health = health_by_id.get(spec.id, {})
        fetches = int(health.get("total_fetches") or 0)
        failures = int(health.get("total_failures") or 0)
        reliability = (fetches - failures) / fetches if fetches else None
        rows.append({
            "id": spec.id,
            "source": "defense",
            "name": spec.extra.name or spec.id,
            "family": spec.family,
            "region": spec.country or "GLOBAL",
            "collector": spec.collector,
            "status": health.get("status", "unverified"),
            "intensity": defense_intensity,
            "cadence": defense_cadence,
            "credibility": spec.credibility,
            "authority_tier": spec.authority_tier,
            "reliability": round(reliability, 3) if reliability is not None else None,
            "next_run": None,
            "last_success_at": _as_iso(health.get("last_success_at")),
            "total_fetches": fetches,
            "total_failures": failures,
        })

    status_order = {"ok": 0, "unverified": 1, "paused": 2, "cooling_down": 3, "pending_disable": 4}
    rows.sort(key=lambda item: (status_order.get(item["status"], 9), -item["intensity"], item["name"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": rows,
        "summary": {
            "total": len(rows),
            "healthy": sum(1 for row in rows if row["status"] in ("ok", "unverified")),
            "high_intensity": sum(1 for row in rows if row["intensity"] >= 70),
            "cooling": sum(1 for row in rows if row["status"] == "cooling_down"),
        },
    }
