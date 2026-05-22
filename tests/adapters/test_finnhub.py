"""Fixture-based tests for FinnhubAdapter.

All HTTP is intercepted at the transport level with respx; no live network
calls are made. Fixtures are loaded from tests/fixtures/finnhub/.

Fixture notes:
- profile.json    — /stock/profile2 shape for ticker ACME.
- candles.json    — /stock/candle shape, status "ok", 5 daily bars
                    for Jan 5-9 2026 UTC midnight timestamps.
- news.json       — /company-news shape, 3 entries.

No real fields were trimmed. Company name, ticker, and numeric values are
fully anonymized ("Acme Corp", "ACME", placeholder figures).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from equity_research_mcp.adapters.finnhub import FinnhubAdapter
from equity_research_mcp.budget import BUCKET_REQUESTS, budget_context, current
from equity_research_mcp.cache import FSCache
from equity_research_mcp.errors import (
    BudgetExceeded,
    MissingCredentials,
    NotFound,
    RateLimited,
    SourceCapabilityError,
    UpstreamError,
)
from equity_research_mcp.schemas import CompanyProfile, NewsItem, PriceBar

FIXTURES = Path(__file__).parent.parent / "fixtures" / "finnhub"
BASE_URL = "https://finnhub.io/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_fixture(name: str) -> object:
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


def make_adapter(tmp_cache_dir: Path, monkeypatch) -> FinnhubAdapter:
    """Construct a FinnhubAdapter with test-only credentials and a temp cache."""
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key-not-real")
    return FinnhubAdapter(cache=FSCache(root=tmp_cache_dir))


# ---------------------------------------------------------------------------
# Test 1 — Happy path: get_company_profile
# ---------------------------------------------------------------------------

@respx.mock
async def test_happy_path_profile(tmp_cache_dir, monkeypatch):
    """Successful /stock/profile2 round-trip returns a populated CompanyProfile."""
    payload = load_fixture("profile.json")
    route = respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}):
        profile = await adapter.get_company_profile("ACME")

    assert route.called
    assert isinstance(profile, CompanyProfile)
    assert profile.ticker == "ACME"
    assert profile.name == "Acme Corp"
    assert profile.exchange == "NASDAQ NMS - GLOBAL MARKET"
    assert profile.industry == "Technology"
    assert profile.sector is None  # /stock/profile2 does not expose sector
    assert profile.source == "finnhub"
    # market_cap = marketCapitalization * 1_000_000
    assert profile.market_cap == Decimal("1234567890000.00")
    # shares_outstanding = int(shareOutstanding * 1_000_000)
    assert profile.shares_outstanding == 1_500_500_000


# ---------------------------------------------------------------------------
# Test 2 — Happy path: get_price_bars
# ---------------------------------------------------------------------------

@respx.mock
async def test_happy_path_candles(tmp_cache_dir, monkeypatch):
    """Successful /stock/candle round-trip returns 5 PriceBars in fixture order."""
    payload = load_fixture("candles.json")
    route = respx.get(f"{BASE_URL}/stock/candle").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}):
        bars = await adapter.get_price_bars("ACME", date(2026, 1, 5), date(2026, 1, 9))

    assert route.called
    assert len(bars) == 5
    for bar in bars:
        assert isinstance(bar, PriceBar)
        assert bar.ticker == "ACME"
        assert bar.source == "finnhub"

    # Verify ordering and date derivation from unix timestamps (UTC midnight)
    expected_dates = [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    ]
    for bar, expected_date in zip(bars, expected_dates):
        assert bar.date == expected_date

    # Spot-check all price/volume fields on bar[0]
    assert isinstance(bars[0].open, Decimal)
    assert isinstance(bars[0].high, Decimal)
    assert isinstance(bars[0].low, Decimal)
    assert isinstance(bars[0].close, Decimal)
    assert isinstance(bars[0].volume, int)
    assert bars[0].volume == 50_000_000


# ---------------------------------------------------------------------------
# Test 3 — Happy path: get_news
# ---------------------------------------------------------------------------

@respx.mock
async def test_happy_path_news(tmp_cache_dir, monkeypatch):
    """Successful /company-news round-trip returns 3 NewsItems with correct fields."""
    payload = load_fixture("news.json")
    route = respx.get(f"{BASE_URL}/company-news").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}):
        items = await adapter.get_news("ACME", date(2026, 1, 5), date(2026, 1, 7))

    assert route.called
    assert len(items) == 3
    for item in items:
        assert isinstance(item, NewsItem)
        assert item.ticker == "ACME"
        assert item.source == "finnhub"
        # published_at must be a UTC-aware datetime
        assert item.published_at.tzinfo is not None
        assert item.published_at.tzinfo == UTC

    # Verify first item in full
    item0 = items[0]
    assert item0.headline == "Acme reports Q4 earnings beat"
    assert item0.url == "https://news.example/articles/acme-q4"
    assert item0.publisher == "Example Wire"  # maps from "source" field in fixture
    assert item0.published_at == datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC)

    # Verify second and third published_at timestamps
    assert items[1].published_at == datetime(2026, 1, 6, 0, 0, 0, tzinfo=UTC)
    assert items[2].published_at == datetime(2026, 1, 7, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test 4 — Missing credentials
# ---------------------------------------------------------------------------

def test_missing_credentials_raises(monkeypatch):
    """FinnhubAdapter raises MissingCredentials at construction if FINNHUB_API_KEY is unset."""
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(MissingCredentials) as exc_info:
        FinnhubAdapter()
    assert exc_info.value.var_name == "FINNHUB_API_KEY"


# ---------------------------------------------------------------------------
# Test 5 — 429 → RateLimited (with and without Retry-After)
# ---------------------------------------------------------------------------

@respx.mock
async def test_rate_limited_with_retry_after(tmp_cache_dir, monkeypatch):
    """HTTP 429 with Retry-After header raises RateLimited with retry_after_seconds set."""
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "60"}, json={}
        )
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(RateLimited) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_company_profile("ACME")

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.retry_after_seconds == 60


@respx.mock
async def test_rate_limited_without_retry_after(tmp_cache_dir, monkeypatch):
    """HTTP 429 without Retry-After header raises RateLimited with retry_after_seconds None."""
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(429, json={})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(RateLimited) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_company_profile("ACME")

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.retry_after_seconds is None


# ---------------------------------------------------------------------------
# Test 6 — Malformed candle payload (mismatched array lengths)
# ---------------------------------------------------------------------------

@respx.mock
async def test_malformed_candle_payload_raises_upstream_error(tmp_cache_dir, monkeypatch):
    """Candle payload where a price array is shorter than t raises UpstreamError.

    The adapter iterates over payload['t'] and indexes into all price arrays by
    position. Dropping the last element of 'o' means len(o)==4 while len(t)==5,
    so payload['o'][4] raises IndexError on the fifth iteration — caught and
    re-raised as UpstreamError.

    Dropping from t instead (len(t)==4) would not trigger an error because the
    adapter only iterates as far as t goes.
    """
    payload = load_fixture("candles.json")
    # Drop last price so len(o) < len(t) — triggers IndexError inside adapter
    bad_payload = dict(payload)
    bad_payload["o"] = payload["o"][:-1]  # 4 opens, 5 timestamps

    respx.get(f"{BASE_URL}/stock/candle").mock(
        return_value=httpx.Response(200, json=bad_payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("ACME", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "finnhub"
    assert "malformed" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 7 — Unknown ticker → NotFound
# ---------------------------------------------------------------------------

@respx.mock
async def test_unknown_ticker_profile_raises_not_found(tmp_cache_dir, monkeypatch):
    """Finnhub returns {} for unknown symbols; adapter raises NotFound."""
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json={})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(NotFound) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_company_profile("ZZZZ")

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.query == "ZZZZ"


@respx.mock
async def test_unknown_ticker_candle_raises_not_found(tmp_cache_dir, monkeypatch):
    """Candle endpoint returning s='no_data' raises NotFound."""
    respx.get(f"{BASE_URL}/stock/candle").mock(
        return_value=httpx.Response(200, json={"s": "no_data"})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(NotFound) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("ZZZZ", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.query == "ZZZZ"


# ---------------------------------------------------------------------------
# Test 8 — SourceCapabilityError for unsupported methods
# ---------------------------------------------------------------------------

async def test_get_filings_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_filings raises SourceCapabilityError because Finnhub doesn't provide filings."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_filings("ACME", ["10-K"], date(2026, 1, 1), date(2026, 1, 31))

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.capability == "filings"


async def test_get_social_mentions_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_social_mentions raises SourceCapabilityError because Finnhub doesn't provide social data."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_social_mentions(
                "ACME", ["wallstreetbets"], date(2026, 1, 1), date(2026, 1, 31)
            )

    assert exc_info.value.source == "finnhub"
    assert exc_info.value.capability == "social_mentions"


# ---------------------------------------------------------------------------
# Test 9 — Budget is charged
# ---------------------------------------------------------------------------

@respx.mock
async def test_budget_is_charged_after_successful_call(tmp_cache_dir, monkeypatch):
    """After a successful get_company_profile call, BUCKET_REQUESTS remaining drops to 0."""
    payload = load_fixture("profile.json")
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}) as budget:
        await adapter.get_company_profile("ACME")
        assert budget.remaining(BUCKET_REQUESTS) == 0


@respx.mock
async def test_budget_exhausted_raises_budget_exceeded(tmp_cache_dir, monkeypatch):
    """A budget of 0 requests causes BudgetExceeded before the HTTP call is made."""
    # The route should NOT be called since budget is exhausted before HTTP fires.
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json={})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(BudgetExceeded):
        with budget_context({BUCKET_REQUESTS: 0}):
            await adapter.get_company_profile("ACME")


# ---------------------------------------------------------------------------
# Test 10 — Normalization: exact Decimal values, no float drift
# ---------------------------------------------------------------------------

@respx.mock
async def test_normalization_close_price_exact_decimal(tmp_cache_dir, monkeypatch):
    """bars[0].close is exactly Decimal('170.25') — string-based conversion, no float drift."""
    payload = load_fixture("candles.json")
    respx.get(f"{BASE_URL}/stock/candle").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}):
        bars = await adapter.get_price_bars("ACME", date(2026, 1, 5), date(2026, 1, 9))

    assert bars[0].close == Decimal("170.25")


@respx.mock
async def test_normalization_market_cap_exact_decimal(tmp_cache_dir, monkeypatch):
    """profile.market_cap is exactly Decimal('1234567890000.00') — no float rounding."""
    payload = load_fixture("profile.json")
    respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 1}):
        profile = await adapter.get_company_profile("ACME")

    # marketCapitalization=1234567.89 in the fixture
    # market_cap = Decimal("1234567.89") * Decimal("1000000") = Decimal("1234567890000.00")
    assert profile.market_cap == Decimal("1234567890000.00")


# ---------------------------------------------------------------------------
# Test 11 — Non-429 4xx → UpstreamError (the path Finnhub /stock/candle
# free-tier 403 exercises live in production)
# ---------------------------------------------------------------------------

@respx.mock
async def test_non_429_4xx_maps_to_upstream_error(tmp_cache_dir, monkeypatch):
    """HTTP 403 (or any non-429 4xx) maps to UpstreamError with the status
    code and response body fragment in the detail. This is the path
    Finnhub's /stock/candle exercises on free-tier keys."""
    respx.get(f"{BASE_URL}/stock/candle").mock(
        return_value=httpx.Response(
            403, text='{"error":"You don\'t have access to this resource."}'
        )
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("ACME", date(2026, 1, 5), date(2026, 1, 9))

    assert exc_info.value.source == "finnhub"
    assert "403" in exc_info.value.detail
    assert "access" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 12 — Cache hit skips the HTTP call AND the budget charge
# ---------------------------------------------------------------------------

@respx.mock
async def test_cache_hit_skips_budget_charge(tmp_cache_dir, monkeypatch):
    """A second call with a pre-warm cache must not charge the budget,
    proving that current().charge(BUCKET_REQUESTS) lives inside the HTTP
    path (not the public method) and is correctly bypassed on cache hits.
    """
    payload = load_fixture("profile.json")
    route = respx.get(f"{BASE_URL}/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)

    # First call: budget 1, charges once on the miss, warms the cache.
    with budget_context({BUCKET_REQUESTS: 1}):
        first = await adapter.get_company_profile("ACME")
        assert current().remaining(BUCKET_REQUESTS) == 0

    # Second call on a fresh budget of 0: cache hit, no charge, no raise.
    with budget_context({BUCKET_REQUESTS: 0}):
        second = await adapter.get_company_profile("ACME")
        assert current().remaining(BUCKET_REQUESTS) == 0

    assert first == second
    assert route.call_count == 1  # network was hit exactly once
