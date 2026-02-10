from __future__ import annotations

import logging
import time

import httpx

from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)

HN_ALGOLIA_API = "https://hn.algolia.com/api/v1"


class HackerNewsSource(BaseSource):
    """HN Algolia Search API client — standalone source + corroboration."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------
    # Standalone source methods
    # ------------------------------------------------------------------

    async def fetch(self) -> list[Event]:
        """Merge front_page + rising stories into Event list."""
        from app.config import settings

        front = await self.fetch_front_page(min_points=settings.hn_front_page_min_points)
        rising = await self.fetch_rising(
            hours_back=settings.hn_rising_hours_back,
            min_points=settings.hn_rising_min_points,
        )

        # Merge by objectID — front_page takes priority
        seen: dict[str, dict] = {}
        for hit in front:
            oid = hit["objectID"]
            hit["discovery_strategy"] = "front_page"
            seen[oid] = hit
        for hit in rising:
            oid = hit["objectID"]
            if oid in seen:
                seen[oid]["discovery_strategy"] = "front_page+rising"
            else:
                hit["discovery_strategy"] = "rising"
                seen[oid] = hit

        events: list[Event] = []
        for hit in seen.values():
            if not hit.get("url") and not hit.get("objectID"):
                continue
            events.append(Event(
                source=SourceType.HACKERNEWS,
                source_id=hit["objectID"],
                data=hit,
                metadata={"strategy": hit.get("discovery_strategy", "unknown")},
            ))

        return events

    async def fetch_front_page(self, min_points: int = 100) -> list[dict]:
        """Fetch current HN front page stories above point threshold."""
        params = {
            "tags": "front_page",
            "hitsPerPage": 30,
        }
        try:
            resp = await self._http.get(f"{HN_ALGOLIA_API}/search", params=params)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            return [
                self._normalize_hit(h) for h in hits
                if h.get("points", 0) >= min_points and h.get("url")
            ]
        except Exception:
            logger.exception("HN fetch_front_page failed")
            return []

    async def fetch_rising(
        self, hours_back: int = 6, min_points: int = 30
    ) -> list[dict]:
        """Fetch recent stories sorted by date, filtered by points."""
        since_ts = int(time.time()) - hours_back * 3600
        params = {
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts},points>{min_points}",
            "hitsPerPage": 30,
        }
        try:
            resp = await self._http.get(
                f"{HN_ALGOLIA_API}/search_by_date", params=params
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            return [
                self._normalize_hit(h) for h in hits
                if h.get("url")
            ]
        except Exception:
            logger.exception("HN fetch_rising failed")
            return []

    async def fetch_item_comments(
        self, object_id: str, limit: int = 5
    ) -> list[dict]:
        """Fetch top comments for a story."""
        params = {
            "tags": f"comment,story_{object_id}",
            "hitsPerPage": limit,
        }
        try:
            resp = await self._http.get(f"{HN_ALGOLIA_API}/search", params=params)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            return [
                {
                    "author": h.get("author", ""),
                    "text": h.get("comment_text", "")[:500],
                    "points": h.get("points", 0),
                    "created_at": h.get("created_at", ""),
                }
                for h in hits
            ]
        except Exception:
            logger.exception("HN fetch_item_comments failed for %s", object_id)
            return []

    # ------------------------------------------------------------------
    # Corroboration search methods (existing)
    # ------------------------------------------------------------------

    async def search_stories(
        self,
        query: str,
        hours_back: int = 72,
        min_points: int = 5,
        limit: int = 10,
    ) -> list[dict]:
        """Search HN stories by relevance."""
        since_ts = int(time.time()) - hours_back * 3600
        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts},points>{min_points}",
            "hitsPerPage": limit,
        }
        try:
            resp = await self._http.get(f"{HN_ALGOLIA_API}/search", params=params)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            return [self._normalize_hit(h) for h in hits]
        except Exception:
            logger.exception("HN search_stories failed for query=%s", query)
            return []

    async def search_by_date(
        self,
        query: str,
        hours_back: int = 48,
        limit: int = 10,
    ) -> list[dict]:
        """Search HN stories by date (most recent first)."""
        since_ts = int(time.time()) - hours_back * 3600
        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts}",
            "hitsPerPage": limit,
        }
        try:
            resp = await self._http.get(
                f"{HN_ALGOLIA_API}/search_by_date", params=params
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            return [self._normalize_hit(h) for h in hits]
        except Exception:
            logger.exception("HN search_by_date failed for query=%s", query)
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_hit(hit: dict) -> dict:
        object_id = hit.get("objectID", "")
        return {
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "points": hit.get("points", 0),
            "num_comments": hit.get("num_comments", 0),
            "author": hit.get("author", ""),
            "created_at": hit.get("created_at", ""),
            "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
            "objectID": object_id,
        }

    async def stop(self) -> None:
        await self._http.aclose()
