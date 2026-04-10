from __future__ import annotations

import hashlib
import importlib
from datetime import datetime, timedelta, timezone

import pytest


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.commands: list[tuple[str, str, int]] = []

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        self.commands.append((key, value, ex or 0))
        return self

    async def execute(self):
        results = []
        for key, value, ex in self.commands:
            created = key not in self.redis.store
            if created:
                self.redis.store[key] = value
                self.redis.ttls[key] = ex
            results.append(created)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self):
        return _FakePipeline(self)

    async def ttl(self, key: str) -> int:
        return self.ttls[key]


def _sample_raw_event(models_mod):
    return models_mod.RawEvent(
        site_id="breakingdefense",
        source_id="breakingdefense:1",
        collector="rss",
        url="https://example.com/post?utm_source=x#fragment",
        title="Hypersonic missile program update",
        body="<p>Body with <b>html</b> content &amp; entities.</p>",
        published_at=None,
        language="en",
        raw_metadata={},
    )


def _sample_spec(models_mod):
    return models_mod.SourceSpec.model_validate(
        {
            "id": "breakingdefense",
            "collector": "rss",
            "country": "US",
            "language": "en",
            "credibility": 0.8,
            "authority_tier": 1,
            "url": "https://breakingdefense.com/feed/",
            "filters": {
                "title_blacklist": ["sponsored"],
                "title_whitelist": ["hypersonic", "missile"],
                "junk_patterns": ["change of command", "ceremony"],
            },
            "extra": {"name": "Breaking Defense"},
        }
    )


def test_task_6_normalizer_contract():
    models_mod = importlib.import_module("app.defense.models")
    normalizer_mod = importlib.import_module("app.defense.normalizer")

    spec = _sample_spec(models_mod)
    raw = _sample_raw_event(models_mod)
    normalized = normalizer_mod.normalize(spec, raw)

    assert normalized.site_id == "breakingdefense"
    assert normalized.canonical_url == "https://example.com/post"
    assert normalized.extraction_quality in {0.7, 1.0}
    assert "<" not in normalized.body
    assert normalized.dedup_keys["url_hash"] == hashlib.md5(normalized.canonical_url.encode()).hexdigest()


@pytest.mark.asyncio
async def test_task_7_deduper_contract():
    models_mod = importlib.import_module("app.defense.models")
    deduper_mod = importlib.import_module("app.defense.deduper")

    event1 = models_mod.NormalizedEvent(
        source_id="s:1",
        site_id="s",
        site_name="Site",
        family="news",
        country="US",
        language="en",
        title="A",
        body="B",
        summary_hint="A",
        url="https://example.com/1",
        canonical_url="https://example.com/1",
        published_at=datetime.now(timezone.utc),
        source_weight=0.8,
        extraction_quality=1.0,
        dedup_keys={"url_hash": "u1", "content_hash": "c1"},
        raw_metadata={},
    )
    event2 = event1.__class__(**{**event1.__dict__, "source_id": "s:2"})

    fake_redis = _FakeRedis()
    deduper = deduper_mod.Deduper(fake_redis, ttl=604800)
    unique = await deduper.filter_duplicates([event1, event2])

    assert len(unique) == 1
    assert await fake_redis.ttl("defense:dedup:url:u1") == 604800


def test_task_8_scorer_contract():
    models_mod = importlib.import_module("app.defense.models")
    scorer_mod = importlib.import_module("app.defense.scorer")

    spec = _sample_spec(models_mod)
    specs_map = {spec.id: spec}
    scorer = scorer_mod.Scorer()

    kept = models_mod.NormalizedEvent(
        source_id="s:1",
        site_id=spec.id,
        site_name="Breaking Defense",
        family="news",
        country="US",
        language="en",
        title="Hypersonic change of command update",
        body="body",
        summary_hint="summary",
        url="https://example.com/1",
        canonical_url="https://example.com/1",
        published_at=datetime.now(timezone.utc),
        source_weight=0.8,
        extraction_quality=1.0,
        dedup_keys={"url_hash": "u1", "content_hash": "c1"},
        raw_metadata={},
    )
    blacklisted = kept.__class__(**{**kept.__dict__, "title": "Sponsored hypersonic update", "source_id": "s:2"})

    filtered = scorer.stage1_filter([kept, blacklisted], specs_map)
    assert kept in filtered
    assert blacklisted not in filtered

    scored = scorer.stage2_score(filtered, specs_map)
    assert scored[0].pre_score > 0

    top = scorer.topk(scored * 3, 2)
    assert len(top) == 2


def test_task_9_converter_contract():
    models_mod = importlib.import_module("app.defense.models")
    converter_mod = importlib.import_module("app.defense.converter")
    app_models = importlib.import_module("app.models")

    normalized = models_mod.NormalizedEvent(
        source_id="breakingdefense:1",
        site_id="breakingdefense",
        site_name="Breaking Defense",
        family="news",
        country="US",
        language="en",
        title="Hypersonic update",
        body="Body",
        summary_hint="Hypersonic update",
        url="https://example.com/1",
        canonical_url="https://example.com/1",
        published_at=datetime.now(timezone.utc),
        source_weight=0.8,
        extraction_quality=1.0,
        dedup_keys={"url_hash": "u1", "content_hash": "c1"},
        raw_metadata={},
        pre_score=0.6,
    )
    event = converter_mod.to_event(normalized)
    assert event.source == app_models.SourceType.DEFENSE
    assert event.data["title"] == "Hypersonic update"
    assert event.metadata["site_id"] == "breakingdefense"


@pytest.mark.asyncio
async def test_milestone_3_checkpoint_pipeline():
    models_mod = importlib.import_module("app.defense.models")
    normalizer_mod = importlib.import_module("app.defense.normalizer")
    deduper_mod = importlib.import_module("app.defense.deduper")
    scorer_mod = importlib.import_module("app.defense.scorer")
    converter_mod = importlib.import_module("app.defense.converter")

    spec = _sample_spec(models_mod)
    raw = _sample_raw_event(models_mod)
    normalized = normalizer_mod.normalize(spec, raw)

    deduper = deduper_mod.Deduper(_FakeRedis(), ttl=604800)
    unique = await deduper.filter_duplicates([normalized])
    filtered = scorer_mod.Scorer().stage1_filter(unique, {spec.id: spec})
    scored = scorer_mod.Scorer().stage2_score(filtered, {spec.id: spec})
    event = converter_mod.to_event(scored[0])

    assert event.source_id == normalized.source_id
