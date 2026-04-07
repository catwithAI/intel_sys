from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


@dataclass
class RawEvent:
    """Direct output from a collector, minimal transformation."""

    site_id: str
    source_id: str
    collector: str
    url: str | None
    title: str
    body: str | None
    published_at: datetime | None
    language: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEvent:
    """Standardized event after normalization, ready for dedup and scoring."""

    source_id: str
    site_id: str
    site_name: str
    family: str
    country: str
    language: str
    title: str
    body: str
    summary_hint: str
    url: str | None
    canonical_url: str | None
    published_at: datetime | None
    source_weight: float
    extraction_quality: float
    dedup_keys: dict[str, str] = field(default_factory=dict)
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    pre_score: float = 0.0


class SourceAccess(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str = "direct"
    risk_level: str = "low"
    allow_fetch: bool = True
    notes: str = ""


class SourceFilters(BaseModel):
    model_config = ConfigDict(extra="allow")

    title_blacklist: list[str] = []
    title_whitelist: list[str] = []
    junk_patterns: list[str] = []


class SourceDedup(BaseModel):
    model_config = ConfigDict(extra="allow")

    canonicalize_url: bool = True
    content_hash: bool = True
    simhash: bool = False


class SourceFetch(BaseModel):
    model_config = ConfigDict(extra="allow")

    timeout_sec: float = 15.0
    max_entries: int = 30
    respect_etag: bool = True
    respect_last_modified: bool = True
    retry_count: int = 2
    negative_ttl_sec: int = 120


class SourceExtra(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    notes: str = ""


class SourceSpec(BaseModel):
    """Declarative source specification loaded from YAML."""

    model_config = ConfigDict(extra="allow")

    id: str
    enabled: bool = True
    family: str = "news"
    tier: str = "p1"
    authority_tier: int = 2
    collector: str = "rss"
    country: str = ""
    language: str = "en"
    credibility: float = 0.5
    url: str = ""

    access: SourceAccess = SourceAccess()
    filters: SourceFilters = SourceFilters()
    dedup: SourceDedup = SourceDedup()
    fetch: SourceFetch = SourceFetch()
    extra: SourceExtra = SourceExtra()


@dataclass
class CollectorResult:
    """Result returned by a collector run."""

    site_id: str
    events: list[RawEvent] = field(default_factory=list)
    status: str = "ok"
    duration_ms: float = 0.0
    record_count: int = 0
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    skipped_reason: str | None = None
