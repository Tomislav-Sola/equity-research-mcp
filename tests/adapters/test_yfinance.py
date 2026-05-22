"""Fixture-based tests for YFinanceAdapter.

The "transport-level boundary" for yfinance (an unofficial library client)
is the module-level _fetch_history helper in adapters/yfinance.py. All tests
patch that function via monkeypatch.setattr or unittest.mock.patch — never
yf.Ticker.history directly, which is nested and version-fragile.

Fixture notes:
- tests/fixtures/yfinance/history.json — 5-row records-format JSON with
  columns Date, Open, High, Low, Close, Volume, Dividends, Stock Splits
  for consecutive trading days 2026-01-05 through 2026-01-09.
  Values are fully anonymized (placeholder OHLCV figures, no real ticker).
  No fields were trimmed.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from equity_research_mcp.adapters.yfinance import YFinanceAdapter
from equity_research_mcp.budget import BUCKET_REQUESTS, budget_context, current
from equity_research_mcp.cache import FSCache
from equity_research_mcp.errors import (
    BudgetExceeded,
    NotFound,
    SourceCapabilityError,
    UpstreamError,
)
from equity_research_mcp.schemas import PriceBar

FIXTURES = Path(__file__).parent.parent / "fixtures" / "yfinance"

_PATCH_TARGET = "equity_research_mcp.adapters.yfinance._fetch_history"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_history_df() -> pd.DataFrame:
    """Rebuild the fixture DataFrame exactly as the adapter would receive it."""
    raw = json.loads((FIXTURES / "history.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(raw)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date")


def make_adapter(tmp_cache_dir: Path) -> YFinanceAdapter:
    """Construct a YFinanceAdapter with an isolated temp-dir cache."""
    return YFinanceAdapter(cache=FSCache(root=tmp_cache_dir))


# ---------------------------------------------------------------------------
# Test 1 — Happy path: 5 PriceBars returned with correct fields
# ---------------------------------------------------------------------------

async def test_happy_path_returns_price_bars(tmp_cache_dir, monkeypatch):
    """Successful _fetch_history returns 5 PriceBar instances in fixture order.

    Asserts ticker, source, date sequence, Decimal types, and the exact
    close value for bar[0] (Decimal('170.25') from the fixture).
    """
    df = load_history_df()
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: df)

    adapter = make_adapter(tmp_cache_dir)
    with budget_context({BUCKET_REQUESTS: 1}):
        bars = await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert len(bars) == 5
    for bar in bars:
        assert isinstance(bar, PriceBar)
        assert bar.ticker == "AAPL"
        assert bar.source == "yfinance"

    expected_dates = [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    ]
    for bar, expected_date in zip(bars, expected_dates):
        assert bar.date == expected_date

    # Decimal-typed prices
    assert isinstance(bars[0].open, Decimal)
    assert isinstance(bars[0].high, Decimal)
    assert isinstance(bars[0].low, Decimal)
    assert isinstance(bars[0].close, Decimal)
    assert isinstance(bars[0].volume, int)

    # Exact close value from fixture row 0
    assert bars[0].close == Decimal("170.25")


# ---------------------------------------------------------------------------
# Test 2 — end+1 day passed to yfinance (exclusive-end compensation)
# ---------------------------------------------------------------------------

async def test_end_plus_one_day_passed_to_fetch_history(tmp_cache_dir, monkeypatch):
    """The adapter must pass end_iso = user_end + 1 day to _fetch_history.

    yfinance's `end` parameter is exclusive. The adapter compensates by
    adding one day so that the user's end date is included in the result.
    """
    df = load_history_df()
    captured: dict[str, str] = {}

    def fake_fetch(ticker: str, start_iso: str, end_iso: str) -> pd.DataFrame:
        captured["end_iso"] = end_iso
        return df

    monkeypatch.setattr(_PATCH_TARGET, fake_fetch)

    adapter = make_adapter(tmp_cache_dir)
    with budget_context({BUCKET_REQUESTS: 1}):
        await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert captured["end_iso"] == "2026-01-10"


# ---------------------------------------------------------------------------
# Test 3 — Empty DataFrame → NotFound
# ---------------------------------------------------------------------------

async def test_empty_dataframe_raises_not_found(tmp_cache_dir, monkeypatch):
    """_fetch_history returning an empty DataFrame raises NotFound.

    source must be 'yfinance' and query must be the uppercased ticker.
    """
    empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: empty_df)

    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(NotFound) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "yfinance"
    assert exc_info.value.query == "AAPL"


# ---------------------------------------------------------------------------
# Test 4 — yfinance exception → UpstreamError
# ---------------------------------------------------------------------------

async def test_runtime_error_from_fetch_raises_upstream_error(tmp_cache_dir, monkeypatch):
    """RuntimeError from _fetch_history is mapped to UpstreamError.

    detail must contain 'yfinance error' and source must be 'yfinance'.
    """
    def raise_runtime(ticker, start_iso, end_iso):
        raise RuntimeError("yahoo went down")

    monkeypatch.setattr(_PATCH_TARGET, raise_runtime)

    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "yfinance"
    assert "yfinance error" in exc_info.value.detail


async def test_value_error_from_fetch_raises_upstream_error(tmp_cache_dir, monkeypatch):
    """ValueError (a non-network error) from _fetch_history is also caught.

    Confirms the broad-except in the adapter covers non-network errors too.
    """
    def raise_value_error(ticker, start_iso, end_iso):
        raise ValueError("unexpected yfinance data shape")

    monkeypatch.setattr(_PATCH_TARGET, raise_value_error)

    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "yfinance"
    assert "yfinance error" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 5 — Malformed DataFrame (missing required column) → UpstreamError
# ---------------------------------------------------------------------------

async def test_malformed_dataframe_missing_volume_raises_upstream_error(
    tmp_cache_dir, monkeypatch
):
    """DataFrame missing 'Volume' column raises UpstreamError with 'malformed' in detail.

    The adapter accesses row['Volume'] during normalization; a KeyError is
    caught and re-raised as UpstreamError(source='yfinance', detail='malformed...').
    """
    # DataFrame with OHLC but no Volume column
    bad_df = pd.DataFrame(
        {
            "Open": [1.0],
            "High": [1.0],
            "Low": [1.0],
            "Close": [1.0],
        },
        index=pd.to_datetime(["2026-01-05"]),
    )
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: bad_df)

    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "yfinance"
    assert "malformed" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 6 — Budget charged and budget exhaustion
# ---------------------------------------------------------------------------

async def test_budget_is_charged_after_successful_call(tmp_cache_dir, monkeypatch):
    """After get_price_bars succeeds, BUCKET_REQUESTS remaining drops to 0."""
    df = load_history_df()
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: df)

    adapter = make_adapter(tmp_cache_dir)
    with budget_context({BUCKET_REQUESTS: 1}) as budget:
        await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))
        assert budget.remaining(BUCKET_REQUESTS) == 0


async def test_budget_zero_raises_budget_exceeded(tmp_cache_dir, monkeypatch):
    """A budget of 0 requests causes BudgetExceeded before _fetch_history is called."""
    called: list[bool] = []

    def should_not_be_called(ticker, start_iso, end_iso):
        called.append(True)
        return load_history_df()

    monkeypatch.setattr(_PATCH_TARGET, should_not_be_called)

    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(BudgetExceeded):
        with budget_context({BUCKET_REQUESTS: 0}):
            await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    # _fetch_history must never have been reached
    assert called == []


# ---------------------------------------------------------------------------
# Test 7 — Cache hit does NOT charge budget
# ---------------------------------------------------------------------------

async def test_cache_hit_skips_budget_charge(tmp_cache_dir, monkeypatch):
    """Second call with budget=0 succeeds via cache; same bars returned.

    First call (budget=1): charges the request, populates the cache.
    Second call (budget=0): reads from cache, no charge, no BudgetExceeded.
    """
    df = load_history_df()
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: df)

    adapter = make_adapter(tmp_cache_dir)

    # First call — charges budget and populates cache
    with budget_context({BUCKET_REQUESTS: 1}):
        bars_first = await adapter.get_price_bars(
            "AAPL", date(2026, 1, 5), date(2026, 1, 9)
        )

    # Second call — budget=0, must succeed via cache without raising
    with budget_context({BUCKET_REQUESTS: 0}):
        bars_second = await adapter.get_price_bars(
            "AAPL", date(2026, 1, 5), date(2026, 1, 9)
        )

    assert len(bars_second) == 5
    for b1, b2 in zip(bars_first, bars_second):
        assert b1.date == b2.date
        assert b1.close == b2.close
        assert b1.open == b2.open
        assert b1.volume == b2.volume
        assert b2.source == "yfinance"


# ---------------------------------------------------------------------------
# Test 8 — SourceCapabilityError for all four unsupported methods
# ---------------------------------------------------------------------------

async def test_get_company_profile_raises_source_capability_error(
    tmp_cache_dir,
):
    """get_company_profile raises SourceCapabilityError(source='yfinance', capability='company_profile')."""
    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_company_profile("AAPL")

    assert exc_info.value.source == "yfinance"
    assert exc_info.value.capability == "company_profile"


async def test_get_news_raises_source_capability_error(tmp_cache_dir):
    """get_news raises SourceCapabilityError(source='yfinance', capability='news')."""
    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_news("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "yfinance"
    assert exc_info.value.capability == "news"


async def test_get_filings_raises_source_capability_error(tmp_cache_dir):
    """get_filings raises SourceCapabilityError(source='yfinance', capability='filings')."""
    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_filings(
                "AAPL", ["10-K"], date(2026, 1, 5), date(2026, 1, 9)
            )

    assert exc_info.value.source == "yfinance"
    assert exc_info.value.capability == "filings"


async def test_get_social_mentions_raises_source_capability_error(tmp_cache_dir):
    """get_social_mentions raises SourceCapabilityError(source='yfinance', capability='social_mentions')."""
    adapter = make_adapter(tmp_cache_dir)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_social_mentions(
                "AAPL", ["wallstreetbets"], date(2026, 1, 5), date(2026, 1, 9)
            )

    assert exc_info.value.source == "yfinance"
    assert exc_info.value.capability == "social_mentions"


# ---------------------------------------------------------------------------
# Test 9 — Decimal normalization preserves precision (no float drift)
# ---------------------------------------------------------------------------

async def test_decimal_normalization_open_price(tmp_cache_dir, monkeypatch):
    """bars[0].open is exactly Decimal('170.0') — Decimal(str(170.0)) is lossless.

    The fixture stores 170.00 as a JSON number, which Python parses as float
    170.0. The adapter converts via Decimal(str(float)), which yields
    Decimal('170.0'). This test confirms there is no float-drift residue
    (e.g. Decimal('169.99999...') or Decimal('170.00000001')).
    """
    df = load_history_df()
    monkeypatch.setattr(_PATCH_TARGET, lambda ticker, start_iso, end_iso: df)

    adapter = make_adapter(tmp_cache_dir)
    with budget_context({BUCKET_REQUESTS: 1}):
        bars = await adapter.get_price_bars("AAPL", date(2026, 1, 5), date(2026, 1, 9))

    # 170.00 in JSON → float 170.0 → str '170.0' → Decimal('170.0')
    assert bars[0].open == Decimal("170.0")
    # Confirm it is NOT a float or string
    assert isinstance(bars[0].open, Decimal)
    # No residual float drift: the Decimal must be exactly equal to 170
    assert bars[0].open == Decimal("170")

    # Spot-check row 1 high: fixture has 172.30 → Decimal('172.3')
    assert bars[1].high == Decimal("172.3")

    # Spot-check volumes are plain int, not float
    assert bars[0].volume == 50_000_000
    assert isinstance(bars[0].volume, int)
