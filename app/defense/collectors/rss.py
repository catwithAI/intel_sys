from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser

from app.defense.models import CollectorResult, RawEvent, SourceSpec
from app.defense.rate_limiter import DomainRateLimiter

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB


class RSSCollector:
    """RSS feed collector using httpx + feedparser."""

    def __init__(self, http: Any, limiter: DomainRateLimiter, min_interval: float = 10.0, redis: Any = None) -> None:
        self._http = http
        self._limiter = limiter
        self._min_interval = min_interval
        self._redis = redis
        self._etag_cache: dict[str, str] = {}
        self._last_modified_cache: dict[str, str] = {}
        self._negative_cache_mem: dict[str, float] = {}

    def _check_negative_cache(self, site_id: str, ttl: int = 120) -> bool:
        """Check negative cache. Uses Redis if available, else in-memory fallback."""
        if self._redis is not None:
            # Async check handled in collect() via _check_negative_cache_async
            return False
        ts = self._negative_cache_mem.get(site_id)
        if ts is None:
            return False
        return (time.monotonic() - ts) < ttl

    async def _check_negative_cache_async(self, site_id: str) -> bool:
        if self._redis is None:
            return False
        key = f"defense:neg:{site_id}"
        return await self._redis.exists(key)

    async def _set_negative_cache_async(self, site_id: str, ttl: int = 120) -> None:
        if self._redis is not None:
            key = f"defense:neg:{site_id}"
            await self._redis.set(key, "1", ex=ttl)
        else:
            self._negative_cache_mem[site_id] = time.monotonic()

    async def collect(self, spec: SourceSpec) -> CollectorResult:
        site_id = spec.id
        start = time.monotonic()

        # Check negative cache: sync (mem) or async (Redis)
        neg_hit = self._check_negative_cache(site_id, spec.fetch.negative_ttl_sec)
        if not neg_hit:
            neg_hit = await self._check_negative_cache_async(site_id)
        if neg_hit:
            return CollectorResult(
                site_id=site_id,
                status="skipped",
                skipped_reason="negative_cache",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        domain = urlparse(spec.url).netloc
        await self._limiter.wait_if_needed(domain, self._min_interval)

        headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
        }
        if spec.fetch.respect_etag and site_id in self._etag_cache:
            headers["If-None-Match"] = self._etag_cache[site_id]
        if spec.fetch.respect_last_modified and site_id in self._last_modified_cache:
            headers["If-Modified-Since"] = self._last_modified_cache[site_id]

        try:
            resp = await self._http.get(
                spec.url,
                headers=headers,
                timeout=spec.fetch.timeout_sec,
            )
        except Exception as exc:
            await self._set_negative_cache_async(site_id, spec.fetch.negative_ttl_sec)
            return CollectorResult(
                site_id=site_id,
                status="error",
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
            )

        http_status = resp.status_code
        duration_ms = (time.monotonic() - start) * 1000

        if resp.headers.get("ETag"):
            self._etag_cache[site_id] = resp.headers["ETag"]
        if resp.headers.get("Last-Modified"):
            self._last_modified_cache[site_id] = resp.headers["Last-Modified"]

        if http_status == 304:
            return CollectorResult(
                site_id=site_id,
                status="not_modified",
                http_status=304,
                etag=self._etag_cache.get(site_id),
                last_modified=self._last_modified_cache.get(site_id),
                duration_ms=duration_ms,
            )

        if http_status >= 400:
            await self._set_negative_cache_async(site_id, spec.fetch.negative_ttl_sec)
            return CollectorResult(
                site_id=site_id,
                status="error",
                http_status=http_status,
                error=f"HTTP {http_status}",
                duration_ms=duration_ms,
            )

        # Content-Length safety check
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_CONTENT_LENGTH:
            await self._set_negative_cache_async(site_id, spec.fetch.negative_ttl_sec)
            return CollectorResult(
                site_id=site_id,
                status="error",
                http_status=http_status,
                error=f"Content-Length {content_length} exceeds {MAX_CONTENT_LENGTH}",
                duration_ms=duration_ms,
            )

        body_text = resp.text
        if len(body_text.encode("utf-8", errors="replace")) > MAX_CONTENT_LENGTH:
            await self._set_negative_cache_async(site_id, spec.fetch.negative_ttl_sec)
            return CollectorResult(
                site_id=site_id,
                status="error",
                http_status=http_status,
                error=f"Response body exceeds {MAX_CONTENT_LENGTH} bytes",
                duration_ms=duration_ms,
            )

        try:
            feed = feedparser.parse(body_text)
        except Exception as exc:
            await self._set_negative_cache_async(site_id, spec.fetch.negative_ttl_sec)
            return CollectorResult(
                site_id=site_id,
                status="error",
                http_status=http_status,
                error=f"Feed parse error: {exc}",
                duration_ms=duration_ms,
            )

        events: list[RawEvent] = []
        max_entries = spec.fetch.max_entries

        for entry in feed.entries[:max_entries]:
            title = getattr(entry, "title", None)
            if not title:
                continue

            link = getattr(entry, "link", None)
            entry_id = getattr(entry, "id", None)

            if entry_id:
                source_id = f"{site_id}:{entry_id}"
            elif link:
                source_id = f"{site_id}:{hashlib.md5(link.encode()).hexdigest()}"
            else:
                # No id and no link — skip this entry
                continue

            body = ""
            if hasattr(entry, "summary"):
                body = entry.summary
            elif hasattr(entry, "content") and entry.content:
                body = entry.content[0].get("value", "")

            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
            if published_at is None:
                published_at = datetime.now(timezone.utc)

            events.append(
                RawEvent(
                    site_id=site_id,
                    source_id=source_id,
                    collector="rss",
                    url=link,
                    title=title,
                    body=body,
                    published_at=published_at,
                    language=spec.language,
                )
            )

        return CollectorResult(
            site_id=site_id,
            events=events,
            status="ok",
            http_status=http_status,
            etag=self._etag_cache.get(site_id),
            last_modified=self._last_modified_cache.get(site_id),
            record_count=len(feed.entries),
            duration_ms=duration_ms,
        )
