from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx
from gtrending import fetch_repos as gtrending_fetch_repos

from app.config import settings
from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)


class GitHubSource(BaseSource):
    """GitHub source: pool-based star monitoring + gtrending integration."""

    def __init__(self) -> None:
        self._token = settings.github_token
        self._topics = settings.github_topics
        self._pool_size = settings.github_pool_size_per_topic
        self._http = httpx.AsyncClient(timeout=30.0)
        self._rate_remaining: int | None = None
        self._rate_reset: int | None = None

    # ------------------------------------------------------------------
    # Rate-limited HTTP helper
    # ------------------------------------------------------------------

    async def _github_get(
        self,
        url: str,
        params: dict | None = None,
        accept: str = "application/vnd.github.v3+json",
    ) -> httpx.Response:
        """GET with rate-limit awareness: pause when near limit, retry on 429."""
        headers = {"Accept": accept}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        # Pre-request rate limit check using cached values
        if self._rate_remaining is not None and self._rate_remaining < 3 and self._rate_reset:
            wait = max(0, self._rate_reset - int(time.time())) + 1
            if wait > 0:
                logger.warning("GitHub rate limit near exhaustion (%s remaining), sleeping %ds",
                               self._rate_remaining, wait)
                await asyncio.sleep(wait)

        resp = await self._http.get(url, headers=headers, params=params)

        # Cache rate-limit headers for next request
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset_ts = resp.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            self._rate_remaining = int(remaining)
        if reset_ts is not None:
            self._rate_reset = int(reset_ts)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            logger.warning("GitHub 429, retrying after %ds", retry_after)
            await asyncio.sleep(retry_after)
            resp = await self._http.get(url, headers=headers, params=params)

        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Pool construction
    # ------------------------------------------------------------------

    async def fetch_pool(self, topic: str, limit: int = 250) -> list[dict]:
        """Fetch top N repos by stars for a given topic (paginated)."""
        pages = math.ceil(limit / 100)
        repos: list[dict] = []

        for page in range(1, pages + 1):
            per_page = min(100, limit - len(repos))
            params = {
                "q": f"topic:{topic} stars:>=1",
                "sort": "stars",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            }
            resp = await self._github_get(
                "https://api.github.com/search/repositories", params=params
            )
            items = resp.json().get("items", [])
            if not items:
                break
            repos.extend(items)
            if len(items) < per_page:
                break

        logger.info("Pool for topic '%s': %d repos", topic, len(repos))
        return repos

    # ------------------------------------------------------------------
    # gtrending
    # ------------------------------------------------------------------

    async def fetch_gtrending(self, languages: list[str]) -> list[Event]:
        """Fetch GitHub Trending via gtrending, filter by min period stars."""
        min_stars = settings.github_gtrending_min_period_stars
        events: list[Event] = []

        for lang in languages:
            try:
                repos = await asyncio.to_thread(
                    gtrending_fetch_repos, language=lang, since="daily"
                )
            except Exception:
                logger.exception("gtrending failed for language=%s", lang)
                continue

            for repo in repos:
                period_stars = repo.get("currentPeriodStars", 0)
                if period_stars < min_stars:
                    continue

                full_name = repo.get("fullname", "")
                event = Event(
                    source=SourceType.GITHUB,
                    source_id=full_name,
                    data={
                        "name": repo.get("name", ""),
                        "full_name": full_name,
                        "description": repo.get("description") or "",
                        "language": repo.get("language") or lang,
                        "stars": repo.get("stars", 0),
                        "forks": repo.get("forks", 0),
                        "current_period_stars": period_stars,
                        "html_url": repo.get("url", f"https://github.com/{full_name}"),
                    },
                    metadata={"strategy": "gtrending"},
                )
                events.append(event)

        logger.info("gtrending fetched %d events across %d languages", len(events), len(languages))
        return events

    # ------------------------------------------------------------------
    # Topic cross-validation
    # ------------------------------------------------------------------

    async def fetch_repo_topics(self, full_name: str) -> list[str]:
        """Fetch a repo's topic labels via REST API."""
        try:
            resp = await self._github_get(
                f"https://api.github.com/repos/{full_name}/topics",
                accept="application/vnd.github.mercy-preview+json",
            )
            return resp.json().get("names", [])
        except Exception:
            logger.warning("Could not fetch topics for %s", full_name)
            return []

    # ------------------------------------------------------------------
    # fetch() — returns pool events (no anomaly detection here)
    # ------------------------------------------------------------------

    async def fetch(self) -> list[Event]:
        """Build observation pool across all configured topics, return Events."""
        events: list[Event] = []
        seen: set[str] = set()

        for topic in self._topics:
            try:
                repos = await self.fetch_pool(topic, self._pool_size)
                for repo in repos:
                    fn = repo["full_name"]
                    if fn in seen:
                        continue
                    seen.add(fn)

                    event = Event(
                        source=SourceType.GITHUB,
                        source_id=fn,
                        data={
                            "name": repo.get("name", ""),
                            "full_name": fn,
                            "description": repo.get("description") or "",
                            "language": repo.get("language") or "",
                            "stars": repo.get("stargazers_count", 0),
                            "forks": repo.get("forks_count", 0),
                            "created_at": repo.get("created_at", ""),
                            "html_url": repo.get("html_url", ""),
                            "topics": repo.get("topics", []),
                            "owner": repo.get("owner", {}).get("login", ""),
                        },
                        metadata={"strategy": "star_delta", "topic": topic},
                    )
                    events.append(event)
            except Exception:
                logger.exception("Failed to build pool for topic: %s", topic)

        logger.info("GitHub pool: %d unique repos across %d topics", len(events), len(self._topics))
        return events

    # ------------------------------------------------------------------
    # README fetcher
    # ------------------------------------------------------------------

    async def fetch_readme(self, full_name: str) -> str:
        """Fetch a repo's README content."""
        try:
            resp = await self._github_get(
                f"https://api.github.com/repos/{full_name}/readme",
                accept="application/vnd.github.v3.raw",
            )
            return resp.text
        except Exception:
            logger.warning("Could not fetch README for %s", full_name)
            return ""

    # ------------------------------------------------------------------
    # Merged PRs fetcher
    # ------------------------------------------------------------------

    async def fetch_merged_prs(
        self, full_name: str, since_ts: int, limit: int = 30
    ) -> list[dict]:
        """Fetch recently merged PRs for a repo since a given timestamp."""
        try:
            resp = await self._github_get(
                f"https://api.github.com/repos/{full_name}/pulls",
                params={
                    "state": "closed",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": min(limit, 100),
                },
            )
            items = resp.json()
        except Exception:
            logger.warning("Could not fetch PRs for %s", full_name)
            return []

        merged: list[dict] = []
        for pr in items:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue
            # Parse ISO 8601 timestamp to epoch
            from datetime import datetime, timezone
            try:
                merged_epoch = int(
                    datetime.fromisoformat(merged_at.replace("Z", "+00:00")).timestamp()
                )
            except (ValueError, TypeError):
                continue
            if merged_epoch <= since_ts:
                continue

            body = pr.get("body") or ""
            merged.append({
                "number": pr["number"],
                "title": pr.get("title", ""),
                "merged_at": merged_at,
                "user": pr.get("user", {}).get("login", ""),
                "html_url": pr.get("html_url", ""),
                "body": body[:500],
                "labels": [lb.get("name", "") for lb in pr.get("labels", [])],
            })

        return merged[:limit]

    async def stop(self) -> None:
        await self._http.aclose()
