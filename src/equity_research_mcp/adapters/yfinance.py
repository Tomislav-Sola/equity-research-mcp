"""Yahoo Finance adapter — daily price bars via the unofficial yfinance library.

yfinance is an unofficial Yahoo Finance scraper, not a supported API.
It needs no key but is best-effort: Yahoo occasionally changes its
endpoints and yfinance breaks until upstream patches. This adapter
serves price bars only; everything else raises SourceCapabilityError.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import yfinance as yf

from ..budget import BUCKET_REQUESTS, current
from ..cache import FSCache
from ..errors import NotFound, SourceCapabilityError, UpstreamError
from ..schemas import (
    CompanyProfile,
    Filing,
    NewsItem,
    PriceBar,
    SocialMention,
)

SOURCE = "yfinance"
TTL_PRICE_SECONDS = 24 * 60 * 60  # 1 day per CLAUDE.md


class YFinanceAdapter:
    name: str = SOURCE

    def __init__(self, cache: FSCache | None = None) -> None:
        # No env vars or API keys required.
        self._cache = cache or FSCache()

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "YFinanceAdapter":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_price_bars(
        self, ticker: str, start: date, end: date
    ) -> list[PriceBar]:
        ticker_u = ticker.upper()
        cache_params = {
            "symbol": ticker_u,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "interval": "1d",
        }
        cached = self._cache.get(SOURCE, "history", cache_params, TTL_PRICE_SECONDS)
        if cached is not None:
            return [_bar_from_cache(entry) for entry in cached]

        current().charge(BUCKET_REQUESTS)
        # yfinance's `end` is exclusive; +1 day so the user's end date is included.
        end_exclusive = (end + timedelta(days=1)).isoformat()
        try:
            df = await asyncio.to_thread(
                _fetch_history,
                ticker_u,
                start.isoformat(),
                end_exclusive,
            )
        except Exception as exc:
            # yfinance raises a wide and version-dependent set of errors
            # (requests exceptions, KeyError, ValueError, version-specific
            # YF* classes). Map them all to UpstreamError — this IS the
            # boundary we're shielding callers from.
            raise UpstreamError(SOURCE, f"yfinance error: {exc}") from exc

        if df is None or df.empty:
            raise NotFound(SOURCE, ticker_u)

        try:
            bars: list[PriceBar] = []
            for idx, row in df.iterrows():
                bar_date = idx.date() if hasattr(idx, "date") else idx
                bars.append(
                    PriceBar(
                        ticker=ticker_u,
                        date=bar_date,
                        open=Decimal(str(row["Open"])),
                        high=Decimal(str(row["High"])),
                        low=Decimal(str(row["Low"])),
                        close=Decimal(str(row["Close"])),
                        volume=int(row["Volume"]),
                        source=SOURCE,
                    )
                )
        except (KeyError, ValueError, TypeError) as exc:
            raise UpstreamError(SOURCE, f"malformed history payload: {exc}") from exc

        self._cache.put(
            SOURCE,
            "history",
            cache_params,
            [b.model_dump(mode="json") for b in bars],
        )
        return bars

    async def get_company_profile(self, ticker: str) -> CompanyProfile:
        raise SourceCapabilityError(SOURCE, "company_profile")

    async def get_news(
        self, ticker: str, start: date, end: date
    ) -> list[NewsItem]:
        raise SourceCapabilityError(SOURCE, "news")

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


def _fetch_history(ticker: str, start_iso: str, end_iso: str) -> Any:
    """Synchronous yfinance call, isolated so the adapter can await it
    via asyncio.to_thread. auto_adjust=False keeps raw OHLCV; split/
    dividend adjustments are out of scope for v0.1.
    """
    return yf.Ticker(ticker).history(
        start=start_iso,
        end=end_iso,
        interval="1d",
        auto_adjust=False,
    )


def _bar_from_cache(entry: dict[str, Any]) -> PriceBar:
    """Rehydrate a cached bar dict back into a PriceBar."""
    return PriceBar(
        ticker=entry["ticker"],
        date=date.fromisoformat(entry["date"]),
        open=Decimal(str(entry["open"])),
        high=Decimal(str(entry["high"])),
        low=Decimal(str(entry["low"])),
        close=Decimal(str(entry["close"])),
        volume=int(entry["volume"]),
        source=entry.get("source", SOURCE),
    )
