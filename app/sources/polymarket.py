from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.models import Event, SourceType
from app.sources.base import BaseSource

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketSource(BaseSource):
    """Polymarket data source using Gamma + CLOB APIs."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        self._active_markets: list[dict] = []
        self._ws_task: asyncio.Task | None = None
        self._clob_semaphore = asyncio.Semaphore(settings.pm_clob_concurrency)

    async def fetch(self) -> list[Event]:
        """Fetch active markets and their current orderbook/price data."""
        await self._refresh_markets()

        top_markets = self._active_markets[:settings.pm_top_markets]

        # Build all (market, token_id) tasks
        tasks: list[asyncio.Task] = []
        for market in top_markets:
            tokens = market.get("clobTokenIds")
            if not tokens:
                continue
            for token_id in (tokens if isinstance(tokens, list) else [tokens]):
                tasks.append(
                    asyncio.create_task(self._fetch_token_data(market, token_id))
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[Event] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Token fetch failed: %s", r)
            elif r is not None:
                events.append(r)

        logger.info(
            "Polymarket source fetched %d events from %d markets (top %d)",
            len(events), len(self._active_markets), len(top_markets),
        )
        return events

    async def fetch_wide(self) -> list[Event]:
        """Tier 1: Fetch all markets from Gamma API with zero CLOB calls.

        Returns Event objects containing Gamma-provided fields for lightweight
        screening (volume, price changes, spread, etc.).
        """
        await self._refresh_markets()

        events: list[Event] = []
        for market in self._active_markets:
            condition_id = market.get("conditionId", market.get("id", ""))
            question = market.get("question", "")
            volume_data = self._extract_volume(market)

            # Parse outcome prices from Gamma
            outcome_prices = market.get("outcomePrices", [])
            best_bid = float(market.get("bestBid", 0) or 0)
            best_ask = float(market.get("bestAsk", 0) or 0)
            spread = best_ask - best_bid if best_ask > 0 and best_bid > 0 else 0.0

            events.append(Event(
                source=SourceType.POLYMARKET,
                source_id=condition_id,
                data={
                    "question": question,
                    "condition_id": condition_id,
                    "volume_24h": volume_data.get("volume_24h", 0),
                    "volume_total": volume_data.get("volume_total", 0),
                    "volume_1wk": float(market.get("volume1wk", 0) or 0),
                    "one_day_price_change": float(market.get("oneDayPriceChange", 0) or 0),
                    "one_hour_price_change": float(market.get("oneHourPriceChange", 0) or 0),
                    "outcome_prices": outcome_prices,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                    "liquidity": float(market.get("liquidity", 0) or 0),
                    "last_trade_price": float(market.get("lastTradePrice", 0) or 0),
                    "clob_token_ids": market.get("clobTokenIds", []),
                    "market_slug": market.get("slug", ""),
                    "event_slug": market.get("_event_slug", ""),
                    "end_date": market.get("endDate", ""),
                    "outcomes": market.get("outcomes", []),
                },
                metadata={
                    "market_id": market.get("id", ""),
                    "category": market.get("category", ""),
                },
            ))

        logger.info(
            "Polymarket wide scan: %d markets from Gamma (zero CLOB calls)",
            len(events),
        )
        return events

    async def fetch_selected(self, markets: list[dict]) -> list[Event]:
        """Tier 2: Fetch CLOB orderbook + midpoint only for pre-selected markets.

        Args:
            markets: List of market dicts (must contain clobTokenIds, conditionId, etc.)
        """
        tasks: list[asyncio.Task] = []
        for market in markets:
            tokens = market.get("clobTokenIds")
            if not tokens:
                continue
            for token_id in (tokens if isinstance(tokens, list) else [tokens]):
                tasks.append(
                    asyncio.create_task(self._fetch_token_data(market, token_id))
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[Event] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Token fetch failed: %s", r)
            elif r is not None:
                events.append(r)

        logger.info(
            "Polymarket selected fetch: %d events from %d markets (%d CLOB calls)",
            len(events), len(markets), len(tasks),
        )
        return events

    async def _fetch_token_data(self, market: dict, token_id: str) -> Event | None:
        """Fetch orderbook + midpoint for a single token concurrently."""
        try:
            book, price = await asyncio.gather(
                self._get_orderbook(token_id),
                self._get_midpoint(token_id),
            )
            volume_data = self._extract_volume(market)
            condition_id = market.get("conditionId", market.get("id", ""))
            question = market.get("question", "")

            return Event(
                source=SourceType.POLYMARKET,
                source_id=condition_id,
                data={
                    "question": question,
                    "token_id": token_id,
                    "condition_id": condition_id,
                    "midpoint_price": price,
                    "orderbook": book,
                    "volume_24h": volume_data.get("volume_24h", 0),
                    "volume_total": volume_data.get("volume_total", 0),
                    "market_slug": market.get("slug", ""),
                    "event_slug": market.get("_event_slug", ""),
                    "end_date": market.get("endDate", ""),
                    "outcomes": market.get("outcomes", []),
                },
                metadata={
                    "market_id": market.get("id", ""),
                    "category": market.get("category", ""),
                },
            )
        except Exception:
            logger.exception("Failed to fetch data for token %s", token_id)
            return None

    async def _refresh_markets(self) -> None:
        """Get list of active markets from Gamma API, filtered and sorted by volume.

        Uses the /markets endpoint directly (instead of /events) so that
        sub-markets belonging to event-level ``closed=True`` parents are still
        discovered.  The endpoint is queried with ``order=volume24hr`` so
        the most active markets come first.
        """
        try:
            resp = await self._http.get(
                f"{GAMMA_API}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": settings.pm_gamma_limit,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            markets = []
            for market in data:
                # Extract parent event info from nested events list
                parent_events = market.get("events", [])
                if parent_events:
                    market["_event_title"] = parent_events[0].get("title", "")
                    market["_event_slug"] = parent_events[0].get("slug", "")
                else:
                    market["_event_title"] = ""
                    market["_event_slug"] = ""

                for field in ("clobTokenIds", "outcomes", "outcomePrices"):
                    val = market.get(field)
                    if isinstance(val, str):
                        try:
                            market[field] = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            pass

                # Pre-filter: skip not accepting orders or no CLOB tokens
                if market.get("acceptingOrders") is False:
                    continue
                if not market.get("clobTokenIds"):
                    continue

                markets.append(market)

            self._active_markets = markets

            top_vol = float(markets[0].get("volume24hr", 0)) if markets else 0
            logger.info(
                "Refreshed %d active markets from /markets endpoint (top: $%s)",
                len(markets),
                f"{top_vol:,.0f}",
            )
        except Exception:
            logger.exception("Failed to refresh Polymarket markets")

    async def _clob_request(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """CLOB API request with semaphore rate limiting and 429 retry."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            async with self._clob_semaphore:
                resp = await self._http.get(url, params=params)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            # 429 — back off and retry
            if attempt < max_retries:
                retry_after = min(
                    float(resp.headers.get("Retry-After", 5)), 30.0
                )
                logger.warning(
                    "CLOB 429 for %s, retrying in %.1fs (attempt %d/%d)",
                    url, retry_after, attempt + 1, max_retries,
                )
                await asyncio.sleep(retry_after)
        # Exhausted retries — raise
        resp.raise_for_status()
        return resp  # unreachable, but keeps type checker happy

    async def _get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Fetch orderbook for a token from CLOB API."""
        try:
            resp = await self._clob_request(
                f"{CLOB_API}/book", params={"token_id": token_id}
            )
            return resp.json()
        except Exception:
            logger.warning("Failed to get orderbook for %s", token_id)
            return {"bids": [], "asks": []}

    async def _get_midpoint(self, token_id: str) -> float:
        """Fetch midpoint price for a token."""
        try:
            resp = await self._clob_request(
                f"{CLOB_API}/midpoint", params={"token_id": token_id}
            )
            data = resp.json()
            return float(data.get("mid", 0))
        except Exception:
            logger.warning("Failed to get midpoint for %s", token_id)
            return 0.0

    @staticmethod
    def _extract_volume(market: dict) -> dict[str, float]:
        """Extract volume info from Gamma market data."""
        try:
            return {
                "volume_24h": float(market.get("volume24hr", 0)),
                "volume_total": float(market.get("volume", 0)),
            }
        except (ValueError, TypeError):
            return {"volume_24h": 0.0, "volume_total": 0.0}

    async def stop(self) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        await self._http.aclose()
