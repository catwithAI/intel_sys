"""Collector registry — maps collector type strings to collector classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.defense.collectors.rss import RSSCollector

COLLECTOR_MAP: dict[str, type] = {}


def register_collectors() -> None:
    from app.defense.collectors.rss import RSSCollector

    COLLECTOR_MAP["rss"] = RSSCollector
