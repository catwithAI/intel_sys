from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.corroboration.query_builder import QueryBuilder
from app.models import Alert
from app.sources.hackernews import HackerNewsSource
from app.sources.twitter import TwitterSource

logger = logging.getLogger(__name__)


@dataclass
class Corroboration:
    hn_stories: list[dict] = field(default_factory=list)
    tweets: list[dict] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
    search_time_ms: int = 0
    confidence_boost: float = 0.0
    has_evidence: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hn_stories": self.hn_stories[:5],
            "tweets": self.tweets[:5],
            "queries_used": self.queries_used,
            "search_time_ms": self.search_time_ms,
            "confidence_boost": self.confidence_boost,
            "has_evidence": self.has_evidence,
            "summary": self.summary,
        }


class CorroborationService:
    """Search HN + Twitter for corroborating evidence of an alert."""

    def __init__(self) -> None:
        self._hn = HackerNewsSource()
        self._twitter: TwitterSource | None = None
        if settings.sm_twitter_api_key:
            self._twitter = TwitterSource(settings.sm_twitter_api_key)
        self._query_builder = QueryBuilder()

    async def search(self, alert: Alert, *, skip_twitter: bool = False) -> Corroboration | None:
        """Search for corroboration with timeout protection.

        Args:
            alert: The alert to search corroboration for.
            skip_twitter: If True, skip Twitter search (useful for high-frequency
                sources like Polymarket to avoid 429 rate limits).
        """
        if not settings.sm_corroboration_enabled:
            return None

        try:
            return await asyncio.wait_for(
                self._do_search(alert, skip_twitter=skip_twitter),
                timeout=settings.sm_corroboration_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Corroboration search timed out for alert %s", alert.id)
            return None
        except Exception:
            logger.exception("Corroboration search failed for alert %s", alert.id)
            return None

    async def _do_search(self, alert: Alert, *, skip_twitter: bool = False) -> Corroboration:
        start = time.monotonic()
        queries = self._query_builder.build(alert)
        if not queries:
            return Corroboration()

        all_hn: list[dict] = []
        all_tweets: list[dict] = []
        queries_used: list[str] = []
        seen_hn_ids: set[str] = set()
        seen_tweet_urls: set[str] = set()

        for query in queries:
            queries_used.append(query)

            # Concurrent HN + Twitter search
            tasks: list[asyncio.Task] = []
            tasks.append(asyncio.create_task(
                self._hn.search_stories(
                    query,
                    hours_back=settings.sm_hn_hours_back,
                    min_points=settings.sm_hn_min_points,
                )
            ))
            if self._twitter and not skip_twitter:
                tasks.append(asyncio.create_task(
                    self._twitter.search_tweets(query, hours_back=settings.sm_hn_hours_back)
                ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process HN results
            hn_results = results[0] if not isinstance(results[0], Exception) else []
            for story in hn_results:
                oid = story.get("objectID", "")
                if oid and oid not in seen_hn_ids:
                    seen_hn_ids.add(oid)
                    all_hn.append(story)

            # Process Twitter results
            if len(results) > 1:
                tw_results = results[1] if not isinstance(results[1], Exception) else []
                for tweet in tw_results:
                    url = tweet.get("url", "")
                    if url and url not in seen_tweet_urls:
                        seen_tweet_urls.add(url)
                        all_tweets.append(tweet)

            # Early exit if we have enough evidence
            if len(all_hn) >= 3 or len(all_tweets) >= 5:
                break

        # Sort HN by points desc, tweets by likes desc
        all_hn.sort(key=lambda s: s.get("points", 0), reverse=True)
        all_tweets.sort(key=lambda t: t.get("likes", 0), reverse=True)

        # Calculate confidence boost
        boost = self._calc_confidence_boost(all_hn[:5], all_tweets[:5])

        # Build summary
        summary = self._build_summary(all_hn[:5], all_tweets[:5], boost)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return Corroboration(
            hn_stories=all_hn[:5],
            tweets=all_tweets[:5],
            queries_used=queries_used,
            search_time_ms=elapsed_ms,
            confidence_boost=boost,
            has_evidence=bool(all_hn or all_tweets),
            summary=summary,
        )

    def _calc_confidence_boost(
        self, hn_stories: list[dict], tweets: list[dict]
    ) -> float:
        boost = 0.0

        # HN contribution
        for story in hn_stories:
            points = story.get("points", 0)
            if points >= 100:
                boost += 0.15
                break
            elif points >= 30:
                boost += 0.10
                break
            elif points >= 5:
                boost += 0.05
                break

        # Twitter contribution
        for tweet in tweets:
            likes = tweet.get("likes", 0)
            followers = tweet.get("followers", 0)
            if likes >= 100 or followers >= 50000:
                boost += 0.10
                break
            elif likes >= 20:
                boost += 0.05
                break

        # Cross-platform bonus
        if hn_stories and tweets:
            boost += 0.05

        # No evidence penalty
        if not hn_stories and not tweets:
            boost = -0.05

        return min(boost, 0.30)

    def _build_summary(
        self, hn_stories: list[dict], tweets: list[dict], boost: float
    ) -> str:
        parts: list[str] = []

        if hn_stories:
            top = hn_stories[0]
            parts.append(
                f"HN 热议: \"{top['title']}\" ({top.get('points', 0)} points, "
                f"{top.get('num_comments', 0)} comments)"
            )
            if len(hn_stories) > 1:
                parts.append(f"另有 {len(hn_stories) - 1} 篇相关 HN 讨论")

        if tweets:
            top = tweets[0]
            author = top.get("author_name") or top.get("author", "")
            parts.append(
                f"Twitter 热议: @{top.get('author', '')} ({author}) "
                f"获 {top.get('likes', 0)} 赞"
            )
            if len(tweets) > 1:
                parts.append(f"另有 {len(tweets) - 1} 条相关推文")

        if not parts:
            parts.append("未找到 Social Media 佐证")

        if boost > 0:
            parts.append(f"置信度提升: +{boost:.2f}")
        elif boost < 0:
            parts.append(f"置信度调整: {boost:.2f}")

        return "；".join(parts)

    async def close(self) -> None:
        await self._hn.stop()
        if self._twitter:
            await self._twitter.stop()
