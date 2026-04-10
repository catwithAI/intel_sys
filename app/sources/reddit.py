from __future__ import annotations

import logging
import time

import httpx

from app.config import settings
from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)


class RedditSource(BaseSource):
    """Reddit OAuth2 API 客户端。"""

    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    API_BASE = "https://oauth.reddit.com"

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)
        self._token: str = ""
        self._token_expires: float = 0

    async def _ensure_token(self) -> None:
        if time.time() < self._token_expires - 60:
            return
        resp = await self._http.post(
            self.TOKEN_URL,
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": settings.reddit_user_agent},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600)

    async def fetch(self) -> list[Event]:
        """获取所有配置 subreddit 的热门帖子。"""
        await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": settings.reddit_user_agent,
        }
        events: list[Event] = []
        for sub in settings.reddit_subreddits:
            try:
                resp = await self._http.get(
                    f"{self.API_BASE}/r/{sub}/hot",
                    params={"limit": str(settings.reddit_fetch_limit)},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                logger.exception("Reddit fetch failed for r/%s", sub)
                continue

            for post in data.get("data", {}).get("children", []):
                d = post.get("data", {})
                score = d.get("score", 0)
                if score < settings.reddit_min_score:
                    continue
                events.append(
                    Event(
                        source=SourceType.REDDIT,
                        source_id=d.get("id", ""),
                        data={
                            "title": d.get("title", ""),
                            "selftext": d.get("selftext", "")[:500],
                            "subreddit": sub,
                            "score": score,
                            "num_comments": d.get("num_comments", 0),
                            "url": d.get("url", ""),
                            "permalink": f"https://reddit.com{d.get('permalink', '')}",
                            "created_utc": d.get("created_utc", 0),
                        },
                    )
                )
        return events

    async def stop(self) -> None:
        await self._http.aclose()
