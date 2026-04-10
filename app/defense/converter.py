from __future__ import annotations

from app.defense.models import NormalizedEvent
from app.models import Event, SourceType


def to_event(normalized: NormalizedEvent) -> Event:
    """Convert a NormalizedEvent to the standard Event model."""
    return Event(
        source=SourceType.DEFENSE,
        source_id=normalized.source_id,
        data={
            "title": normalized.title,
            "title_zh": normalized.title_zh,
            "summary_zh": normalized.summary_zh,
            "content": normalized.body,
            "summary_hint": normalized.summary_hint,
            "url": normalized.url,
            "canonical_url": normalized.canonical_url,
            "country": normalized.country,
            "language": normalized.language,
            "pre_score": normalized.pre_score,
        },
        metadata={
            "site_id": normalized.site_id,
            "site_name": normalized.site_name,
            "family": normalized.family,
            "source_weight": normalized.source_weight,
            "extraction_quality": normalized.extraction_quality,
            "dedup_keys": normalized.dedup_keys,
        },
    )
