from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import fields

import pytest

from tests.defense_tasks.conftest import read_project_text


def test_task_1_integration_surface():
    models_mod = importlib.import_module("app.models")
    assert hasattr(models_mod.SourceType, "DEFENSE")
    assert models_mod.SourceType.DEFENSE.value == "defense"

    config_mod = importlib.import_module("app.config")
    settings = config_mod.Settings()
    for attr in (
        "defense_rss_interval",
        "defense_rss_concurrency",
        "defense_domain_min_interval",
        "defense_rss_timeout",
        "defense_topk",
        "defense_dedup_ttl",
        "pg_dsn",
        "pg_pool_min",
        "pg_pool_max",
        "feishu_defense_webhook_url",
    ):
        assert hasattr(settings, attr), attr

    context_mod = importlib.import_module("app.engine.context")
    assert "app_state" in {f.name for f in fields(context_mod.RuleContext)}

    pyproject = read_project_text("pyproject.toml").lower()
    assert "feedparser" in pyproject
    assert "asyncpg" in pyproject
    assert "pyyaml" in pyproject

    plan_text = read_project_text("docs/defense-integration-plan.md")
    phase1_section = plan_text.split("## 3. Phase 1 范围重定义", 1)[1].split("## 4.", 1)[0]
    assert "memory-first + selective alert" in phase1_section


def test_task_2_defense_models_contract():
    defense_models = importlib.import_module("app.defense.models")

    raw = defense_models.RawEvent(
        site_id="breakingdefense",
        source_id="breakingdefense:abc123",
        collector="rss",
        url="https://example.com/post",
        title="Hypersonic update",
        body="Body",
        published_at=None,
        language="en",
    )
    assert raw.site_id == "breakingdefense"

    normalized = defense_models.NormalizedEvent(
        source_id="breakingdefense:abc123",
        site_id="breakingdefense",
        site_name="Breaking Defense",
        family="news",
        country="US",
        language="en",
        title="Hypersonic update",
        body="Body",
        summary_hint="Hypersonic update",
        url="https://example.com/post",
        canonical_url="https://example.com/post",
        published_at=None,
        source_weight=0.8,
        extraction_quality=1.0,
        dedup_keys={"url_hash": "u", "content_hash": "c"},
        raw_metadata={},
    )
    assert normalized.site_name == "Breaking Defense"

    spec = defense_models.SourceSpec.model_validate(
        {
            "id": "breakingdefense",
            "collector": "rss",
            "country": "US",
            "url": "https://breakingdefense.com/feed/",
            "schedule": "interval:30m",
            "unknown_field": "ignored",
        }
    )
    assert spec.id == "breakingdefense"
    assert spec.collector == "rss"

    result = defense_models.CollectorResult(
        site_id="breakingdefense",
        events=[raw],
        status="ok",
        duration_ms=12.5,
    )
    assert result.record_count == 0
    assert result.events[0].source_id == "breakingdefense:abc123"


@pytest.mark.asyncio
async def test_task_3_rate_limiter_behaviour():
    rate_limiter_mod = importlib.import_module("app.defense.rate_limiter")
    limiter = rate_limiter_mod.DomainRateLimiter()

    min_interval = 0.05
    start = time.monotonic()
    await limiter.wait_if_needed("example.com", min_interval)
    await limiter.wait_if_needed("example.com", min_interval)
    same_domain_elapsed = time.monotonic() - start
    assert same_domain_elapsed >= min_interval

    start = time.monotonic()
    await asyncio.gather(
        limiter.wait_if_needed("a.example.com", min_interval),
        limiter.wait_if_needed("b.example.com", min_interval),
    )
    cross_domain_elapsed = time.monotonic() - start
    assert cross_domain_elapsed < min_interval * 1.8


def test_milestone_1_checkpoint():
    models_mod = importlib.import_module("app.models")
    assert hasattr(models_mod.SourceType, "DEFENSE")

    context_mod = importlib.import_module("app.engine.context")
    assert "app_state" in {f.name for f in fields(context_mod.RuleContext)}

    importlib.import_module("app.defense.models")
    importlib.import_module("app.defense.rate_limiter")
