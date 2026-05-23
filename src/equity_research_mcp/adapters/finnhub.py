"""Finnhub adapter — implements the SourceAdapter Protocol.

Capabilities provided: company profile, daily price bars, company news.
Filings and social mentions raise SourceCapabilityError per the
adapters/CLAUDE.md contract.

Daily resolution only at v0.1 — the Protocol has no `resolution`
parameter. Intraday support would be a Protocol change in v0.2.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from ..budget import BUCKET_REQUESTS, current
from ..cache import FSCache
from ..errors import (
    MissingCredentials,
    NotFound,
    RateLimited,
    SourceCapabilityError,
    UpstreamError,
)
from ..schemas import CompanyProfile, Filing, NewsItem, PriceBar, SocialMention

FINNHUB_BASE = "https://finnhub.io/api/v1"
SOURCE = "finnhub"

# Per-source TTLs. CLAUDE.md fixes price=1d, news=4h, filings=1w,
# profile=1w. 7d matches the cadence at which company metadata (sector,
# exchange, share count) actually changes.
TTL_PROFILE_SECONDS = 7 * 24 * 60 * 60
TTL_PRICE_SECONDS = 24 * 60 * 60
TTL_NEWS_SECONDS = 4 * 60 * 60


class FinnhubAdapter:
    name: str = SOURCE

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache: FSCache | None = None,
    ) -> None:
        api_key = os.environ.get("FINNHUB_API_KEY")
        if not api_key:
            raise MissingCredentials("FINNHUB_API_KEY")
        self._api_key = api_key
        if client is None:
            self._client = httpx.AsyncClient(base_url=FINNHUB_BASE, timeout=30.0)
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False
        self._cache = cache or FSCache()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "FinnhubAdapter":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        current().charge(BUCKET_REQUESTS)
        merged = {**params, "token": self._api_key}
        try:
            response = await self._client.get(path, params=merged)
        except httpx.HTTPError as exc:
            raise UpstreamError(SOURCE, f"network error: {exc}") from exc
        if response.status_code == 429:
            retry = response.headers.get("Retry-After")
            seconds = int(retry) if retry and retry.isdigit() else None
            raise RateLimited(SOURCE, retry_after_seconds=seconds)
        if response.status_code >= 400:
            raise UpstreamError(
                SOURCE, f"HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(SOURCE, f"non-JSON response: {exc}") from exc

    async def get_company_profile(self, ticker: str) -> CompanyProfile:
        ticker_u = ticker.upper()
        params = {"symbol": ticker_u}
        cached = self._cache.get(SOURCE, "profile", params, TTL_PROFILE_SECONDS)
        if cached is not None:
            payload = cached
        else:
            payload = await self._get("/stock/profile2", params)
            self._cache.put(SOURCE, "profile", params, payload)
        # Finnhub returns {} for unknown tickers (no error status).
        if not payload or not payload.get("name"):
            raise NotFound(SOURCE, ticker_u)
        try:
            market_cap = (
                Decimal(str(payload["marketCapitalization"])) * Decimal("1000000")
                if payload.get("marketCapitalization") is not None
                else None
            )
            shares_out = (
                int(payload["shareOutstanding"] * 1_000_000)
                if payload.get("shareOutstanding") is not None
                else None
            )
            return CompanyProfile(
                ticker=ticker_u,
                name=payload["name"],
                exchange=payload.get("exchange"),
                sector=None,  # /stock/profile2 doesn't expose sector
                industry=payload.get("finnhubIndustry"),
                market_cap=market_cap,
                shares_outstanding=shares_out,
                float_shares=None,  # /stock/profile2 doesn't expose float
                country=payload.get("country"),
                source=SOURCE,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(SOURCE, f"malformed profile payload: {exc}") from exc

    async def get_price_bars(
        self, ticker: str, start: date, end: date
    ) -> list[PriceBar]:
        ticker_u = ticker.upper()
        start_ts = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
        end_ts = int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp())
        params = {
            "symbol": ticker_u,
            "resolution": "D",
            "from": start_ts,
            "to": end_ts,
        }
        cached = self._cache.get(SOURCE, "candle", params, TTL_PRICE_SECONDS)
        if cached is not None:
            payload = cached
        else:
            payload = await self._get("/stock/candle", params)
            self._cache.put(SOURCE, "candle", params, payload)
        status = payload.get("s") if isinstance(payload, dict) else None
        if status == "no_data":
            raise NotFound(SOURCE, ticker_u)
        if status != "ok":
            raise UpstreamError(SOURCE, f"unexpected candle status: {status!r}")
        try:
            bars: list[PriceBar] = []
            for i, ts in enumerate(payload["t"]):
                bars.append(
                    PriceBar(
                        ticker=ticker_u,
                        date=datetime.fromtimestamp(ts, tz=UTC).date(),
                        open=Decimal(str(payload["o"][i])),
                        high=Decimal(str(payload["h"][i])),
                        low=Decimal(str(payload["l"][i])),
                        close=Decimal(str(payload["c"][i])),
                        volume=int(payload["v"][i]),
                        source=SOURCE,
                    )
                )
            return bars
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise UpstreamError(SOURCE, f"malformed candle payload: {exc}") from exc

    async def get_news(
        self, ticker: str, start: date, end: date
    ) -> list[NewsItem]:
        ticker_u = ticker.upper()
        params = {
            "symbol": ticker_u,
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
        cached = self._cache.get(SOURCE, "news", params, TTL_NEWS_SECONDS)
        if cached is not None:
            payload = cached
        else:
            payload = await self._get("/company-news", params)
            self._cache.put(SOURCE, "news", params, payload)
        if not isinstance(payload, list):
            raise UpstreamError(
                SOURCE, f"expected news list, got {type(payload).__name__}"
            )
        try:
            return [
                NewsItem(
                    ticker=ticker_u,
                    headline=entry["headline"],
                    url=entry["url"],
                    published_at=datetime.fromtimestamp(entry["datetime"], tz=UTC),
                    publisher=entry.get("source"),
                    summary=entry.get("summary"),
                    source=SOURCE,
                )
                for entry in payload
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(SOURCE, f"malformed news entry: {exc}") from exc

    async def get_filings(
        self,
        ticker: str,
        form_types: list[str],
        start: date,
        end: date,
    ) -> list[Filing]:
        raise SourceCapabilityError(SOURCE, "filings")

    async def get_social_mentions(
        self,
        ticker: str,
        subreddits: list[str],
        start: date,
        end: date,
    ) -> list[SocialMention]:
        raise SourceCapabilityError(SOURCE, "social_mentions")
