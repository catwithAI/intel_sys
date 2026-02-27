from __future__ import annotations

import hashlib
import logging

import httpx

from app.config import settings
from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)


class CLSNewsSource(BaseSource):
    """财联社电报列表 API 客户端。"""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)

    @staticmethod
    def _gen_sign(params: dict) -> str:
        """params sorted by key -> URL encode -> SHA1 -> MD5."""
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
        sha1 = hashlib.sha1(query_str.encode()).hexdigest()
        return hashlib.md5(sha1.encode()).hexdigest()

    async def fetch(self) -> list[Event]:
        """获取最新电报列表，返回 Event 列表。"""
        params = {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": "8.4.6",
            "rn": str(settings.cls_fetch_limit),
        }
        params["sign"] = self._gen_sign(params)

        try:
            resp = await self._http.get(
                f"{settings.cls_base_url}/telegraphList", params=params
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("CLS fetch failed")
            return []

        rolls = data.get("data", {}).get("roll_data", [])

        events = []
        for item in rolls:
            content = item.get("content", "")
            title = item.get("title", "") or content[:80]
            events.append(
                Event(
                    source=SourceType.CLS,
                    source_id=str(item.get("id", "")),
                    data={
                        "title": title,
                        "content": content,
                        "ctime": item.get("ctime", 0),
                        "subjects": [
                            s.get("subject_name", "")
                            for s in (item.get("subjects") or [])
                        ],
                    },
                )
            )
        return events

    async def stop(self) -> None:
        await self._http.aclose()
