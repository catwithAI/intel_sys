from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from app.defense.models import NormalizedEvent, RawEvent, SourceSpec

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source", "fbclid", "gclid"}
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = HTML_TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _canonicalize_url(url: str | None) -> str | None:
    """Remove tracking params, fragment, trailing slash. Non-http(s) → None."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    params = parse_qs(parsed.query, keep_blank_values=False)
    cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    new_query = urlencode(cleaned, doseq=True) if cleaned else ""
    path = parsed.path.rstrip("/") or "/"
    result = urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, new_query, ""))
    return result


def _compute_quality(raw: RawEvent) -> float:
    """Compute extraction quality based on title + body/summary length."""
    title = raw.title or ""
    body = raw.body or ""
    body_clean = _strip_html(body)
    total_len = len(title) + len(body_clean)

    if total_len >= 200:
        return 1.0
    if total_len >= 50:
        return 0.7
    return 0.4


def normalize(spec: SourceSpec, raw: RawEvent) -> NormalizedEvent:
    """Normalize a RawEvent into a NormalizedEvent."""
    canonical_url = _canonicalize_url(raw.url)
    body_clean = _strip_html(raw.body) if raw.body else ""
    quality = _compute_quality(raw)

    # Handle published_at
    published_at = raw.published_at
    if published_at is not None:
        # Ensure timezone-aware
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        # Future time (> now + 1h) → replace with now
        if published_at > datetime.now(timezone.utc) + timedelta(hours=1):
            published_at = datetime.now(timezone.utc)

    # Generate dedup keys
    url_hash = hashlib.md5(canonical_url.encode()).hexdigest() if canonical_url else ""
    content_hash = hashlib.md5(f"{raw.title}:{body_clean[:500]}".encode()).hexdigest()

    site_name = spec.extra.name if spec.extra.name else spec.id

    return NormalizedEvent(
        source_id=raw.source_id,
        site_id=raw.site_id,
        site_name=site_name,
        family=spec.family,
        country=spec.country,
        language=raw.language or spec.language,
        title=raw.title,
        body=body_clean,
        summary_hint=raw.title[:200],
        url=raw.url,
        canonical_url=canonical_url,
        published_at=published_at,
        source_weight=spec.credibility,
        extraction_quality=quality,
        dedup_keys={"url_hash": url_hash, "content_hash": content_hash},
        raw_metadata=raw.raw_metadata,
    )
