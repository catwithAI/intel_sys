from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    POLYMARKET = "polymarket"
    GITHUB = "github"
    HACKERNEWS = "hackernews"
    TWITTER = "twitter"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Event(BaseModel):
    """Standardized data unit produced by a Source."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: SourceType
    source_id: str  # e.g. market condition_id or repo full_name
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AIEnrichment(BaseModel):
    """Structured output from LLM analysis."""

    summary: str = ""
    analysis: str = ""
    confidence: float = 0.0
    raw_response: str = ""


class Alert(BaseModel):
    """Final product of Rule + AI pipeline."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: SourceType
    rule_name: str
    severity: Severity = Severity.MEDIUM
    title: str = ""
    event: Event
    enrichment: AIEnrichment = Field(default_factory=AIEnrichment)
    corroboration: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuleConfig(BaseModel):
    """Per-rule configuration, injected via RuleContext."""

    name: str
    source: SourceType
    params: dict[str, Any] = Field(default_factory=dict)
