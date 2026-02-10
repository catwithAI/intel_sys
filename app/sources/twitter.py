from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from app.models import Event
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)

TWITTER_API_BASE = "https://api.twitterapi.io/twitter/tweet"


class TwitterSource(BaseSource):
    """twitterapi.io client for corroboration queries."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=10.0)

    async def fetch(self) -> list[Event]:
        """Not used — Twitter is corroboration-only, not a standalone source."""
        return []

    async def search_tweets(
        self,
        query: str,
        max_results: int = 20,
        hours_back: int = 72,
    ) -> list[dict]:
        """Search recent tweets via twitterapi.io advanced_search."""
        if not self._api_key:
            return []

        # Build date filter for the query
        since_dt = datetime.fromtimestamp(
            time.time() - hours_back * 3600, tz=timezone.utc
        )
        since_str = since_dt.strftime("%Y-%m-%d")
        full_query = f"{query} since:{since_str}"

        params = {
            "query": full_query,
            "queryType": "Latest",
            "cursor": "",
        }
        headers = {"X-API-Key": self._api_key}

        try:
            resp = await self._http.get(
                f"{TWITTER_API_BASE}/advanced_search",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            tweets = data.get("tweets", [])
            return [self._normalize_tweet(t) for t in tweets[:max_results]]
        except Exception:
            logger.exception("Twitter search failed for query=%s", query)
            return []

    @staticmethod
    def _normalize_tweet(tweet: dict) -> dict:
        author_info = tweet.get("author", {})
        return {
            "text": tweet.get("text", ""),
            "author": author_info.get("userName", ""),
            "author_name": author_info.get("name", ""),
            "followers": author_info.get("followers", 0),
            "likes": tweet.get("likeCount", 0),
            "retweets": tweet.get("retweetCount", 0),
            "created_at": tweet.get("createdAt", ""),
            "url": tweet.get("url", ""),
        }

    async def stop(self) -> None:
        await self._http.aclose()
