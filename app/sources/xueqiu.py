from __future__ import annotations

import logging
import re

import httpx

from app.config import settings
from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)


class XueqiuSource(BaseSource):
    """雪球 7x24 快讯 API 客户端。"""

    API_BASE = "https://xueqiu.com"

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)

    async def fetch(self) -> list[Event]:
        """获取雪球 7x24 快讯，返回 Event 列表。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Cookie": settings.xueqiu_cookie,
            "Referer": "https://xueqiu.com/",
        }

        try:
            resp = await self._http.get(
                f"{self.API_BASE}/v4/statuses/public_timeline_by_category.json",
                params={
                    "since_id": -1,
                    "max_id": -1,
                    "count": settings.xueqiu_fetch_limit,
                    "category": -1,
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Xueqiu fetch failed")
            return []

        items = data.get("list", [])
        events: list[Event] = []

        for item in items:
            status_id = str(item.get("id", ""))
            if not status_id:
                continue

            title = item.get("title", "") or ""
            text = item.get("text", "") or ""
            description = item.get("description", "") or ""
            # Use title if available, otherwise first 80 chars of text/description
            display_title = title or description[:80] or text[:80]
            content = description or text

            # Strip HTML tags from content
            content = re.sub(r"<[^>]+>", "", content)

            created_at = item.get("created_at", 0)
            # Xueqiu returns milliseconds timestamp
            if created_at and created_at > 1e12:
                created_at = int(created_at / 1000)

            user_info = item.get("user", {}) or {}
            screen_name = user_info.get("screen_name", "")

            events.append(
                Event(
                    source=SourceType.XUEQIU,
                    source_id=status_id,
                    data={
                        "title": display_title,
                        "content": content[:500],
                        "created_at": created_at,
                        "author": screen_name,
                        "url": f"https://xueqiu.com/{user_info.get('id', '')}/{status_id}"
                        if user_info.get("id")
                        else "",
                    },
                )
            )

        return events

    async def stop(self) -> None:
        await self._http.aclose()
