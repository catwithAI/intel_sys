from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.defense_tasks.conftest import PROJECT_ROOT


class _DummyLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    async def wait_if_needed(self, domain: str, min_interval: float) -> None:
        self.calls.append((domain, min_interval))


class _DummyResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _DummyHttp:
    def __init__(self, response: _DummyResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def get(self, url: str, headers: dict | None = None, timeout: float | None = None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return self.response


@pytest.mark.asyncio
async def test_task_4_rss_collector_contract(monkeypatch):
    models_mod = importlib.import_module("app.defense.models")
    rss_mod = importlib.import_module("app.defense.collectors.rss")
    registry_mod = importlib.import_module("app.defense.collectors.registry")

    assert registry_mod is not None

    spec = models_mod.SourceSpec.model_validate(
        {
            "id": "breakingdefense",
            "collector": "rss",
            "country": "US",
            "url": "https://breakingdefense.com/feed/",
        }
    )

    limiter = _DummyLimiter()
    http = _DummyHttp(_DummyResponse(304, headers={"ETag": "etag-1"}))
    collector = rss_mod.RSSCollector(http, limiter)

    monkeypatch.setattr(collector, "_check_negative_cache", lambda *_args, **_kwargs: False, raising=False)
    result = await collector.collect(spec)
    assert result.status == "not_modified"
    assert result.events == []

    monkeypatch.setattr(collector, "_check_negative_cache", lambda *_args, **_kwargs: True, raising=False)
    skipped = await collector.collect(spec)
    assert skipped.status == "skipped"
    assert skipped.skipped_reason == "negative_cache"


def test_task_5_source_loader_and_seed_config(tmp_path: Path):
    source_loader_mod = importlib.import_module("app.defense.source_loader")

    (tmp_path / "defense_news.yaml").write_text(
        """
- id: breakingdefense
  collector: rss
  country: US
  url: https://breakingdefense.com/feed/
- id: disabled-source
  enabled: false
  collector: rss
  country: US
  url: https://example.com/disabled.xml
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "defense_extra.yaml").write_text(
        """
- id: duplicate-id
  collector: rss
  country: US
  url: https://example.com/one.xml
- id: duplicate-id
  collector: rss
  country: US
  url: https://example.com/two.xml
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "github.yaml").write_text("[]", encoding="utf-8")

    specs = source_loader_mod.SourceLoader.load_defense_sources(str(tmp_path))
    ids = [spec.id for spec in specs]
    assert "breakingdefense" in ids
    assert "disabled-source" not in ids

    seed_path = PROJECT_ROOT / "sources" / "defense_news.yaml"
    assert seed_path.exists()


def test_milestone_2_checkpoint():
    importlib.import_module("app.defense.source_loader")
    importlib.import_module("app.defense.collectors.rss")
    assert (PROJECT_ROOT / "sources" / "defense_news.yaml").exists()
