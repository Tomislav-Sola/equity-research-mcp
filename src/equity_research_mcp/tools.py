"""Phase 2 research tools — thin wrappers over the Finnhub adapter.

Each tool enters a fresh budget context (requests bucket only at v0.1)
and dispatches to the adapter. Statistics computed in the tool layer,
not the adapter — adapters return raw normalized data.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from .adapters.finnhub import FinnhubAdapter
from .adapters.yfinance import YFinanceAdapter
from .budget import BUCKET_REQUESTS, budget_context

# Generous per-call budget: Phase 2 tools make exactly one HTTP call,
# the headroom is for v0.2 retries and v0.3 aggregator fan-out.
DEFAULT_LIMITS = {BUCKET_REQUESTS: 5}

# 30 trading days ≈ 45 calendar days; pad covers weekends and US holidays.
ZSCORE_LOOKBACK_TRADING = 30
ZSCORE_LOOKBACK_CALENDAR_DAYS = 45


async def get_company_profile(ticker: str) -> dict[str, Any]:
    """Return basic company metadata for a US ticker."""
    with budget_context(DEFAULT_LIMITS):
        async with FinnhubAdapter() as fin:
            profile = await fin.get_company_profile(ticker)
    return profile.model_dump(mode="json")


async def get_price_action(ticker: str, start: str, end: str) -> dict[str, Any]:
    """Return daily price bars over [start, end] (ISO dates) plus a
    volume z-score per bar versus the prior 30-trading-day average.

    Bars before `start` are fetched only as a baseline window and are
    not included in the response. Z-score is None when fewer than 30
    prior bars are available, or when the stdev of the window is zero.

    Backed by yfinance (best-effort, unofficial Yahoo client). Finnhub's
    candle endpoint is paid-tier only, so price bars come from yfinance
    even though profile and news are still Finnhub-backed.
    """
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    fetch_start = start_d - timedelta(days=ZSCORE_LOOKBACK_CALENDAR_DAYS)

    with budget_context(DEFAULT_LIMITS):
        async with YFinanceAdapter() as src:
            all_bars = await src.get_price_bars(ticker, fetch_start, end_d)

    all_bars_sorted = sorted(all_bars, key=lambda b: b.date)
    in_range = [b for b in all_bars_sorted if start_d <= b.date <= end_d]
    out_bars: list[dict[str, Any]] = []
    for bar in in_range:
        prior_volumes = [
            b.volume for b in all_bars_sorted if b.date < bar.date
        ][-ZSCORE_LOOKBACK_TRADING:]
        if len(prior_volumes) >= ZSCORE_LOOKBACK_TRADING:
            mean = statistics.fmean(prior_volumes)
            stdev = statistics.pstdev(prior_volumes)
            z: float | None = (bar.volume - mean) / stdev if stdev > 0 else None
        else:
            z = None
        d = bar.model_dump(mode="json")
        d["volume_zscore_30d"] = z
        out_bars.append(d)

    return {
        "ticker": ticker.upper(),
        "start": start,
        "end": end,
        "bars": out_bars,
        "source": "yfinance",
    }


async def get_news(ticker: str, start: str, end: str) -> dict[str, Any]:
    """Return company news headlines over [start, end] (ISO dates)."""
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    with budget_context(DEFAULT_LIMITS):
        async with FinnhubAdapter() as fin:
            items = await fin.get_news(ticker, start_d, end_d)
    return {
        "ticker": ticker.upper(),
        "start": start,
        "end": end,
        "items": [item.model_dump(mode="json") for item in items],
        "source": "finnhub",
    }
