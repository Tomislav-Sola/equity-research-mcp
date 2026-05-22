from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from equity_research_mcp.schemas import (
    CompanyProfile,
    Filing,
    NewsItem,
    PriceBar,
    SocialMention,
)


def test_company_profile_minimal():
    cp = CompanyProfile(ticker="AAPL", name="Apple Inc.", source="finnhub")
    assert cp.ticker == "AAPL"
    assert cp.market_cap is None


def test_company_profile_full():
    cp = CompanyProfile(
        ticker="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        sector="Technology",
        market_cap=Decimal("3000000000000"),
        shares_outstanding=15_000_000_000,
        source="finnhub",
    )
    assert cp.market_cap == Decimal("3000000000000")


def test_filing_defaults_source():
    f = Filing(
        ticker="AAPL",
        form_type="4",
        filed_at=datetime(2026, 5, 1, 12, 0, 0),
        accession_number="0000000000-26-000001",
        url="https://www.sec.gov/...",
    )
    assert f.source == "edgar"


def test_price_bar():
    pb = PriceBar(
        ticker="AAPL",
        date=date(2026, 5, 1),
        open=Decimal("170.00"),
        high=Decimal("172.50"),
        low=Decimal("169.80"),
        close=Decimal("171.25"),
        volume=50_000_000,
        source="finnhub",
    )
    assert pb.close == Decimal("171.25")


def test_news_item():
    n = NewsItem(
        ticker="AAPL",
        headline="Apple reports Q2 earnings",
        url="https://example.com/article",
        published_at=datetime(2026, 5, 1, 16, 0, 0),
        source="finnhub",
    )
    assert n.publisher is None


def test_social_mention_defaults_source():
    sm = SocialMention(
        ticker="AAPL",
        subreddit="stocks",
        post_id="abc123",
        title="AAPL discussion",
        url="https://reddit.com/r/stocks/comments/abc123",
        score=42,
        num_comments=10,
        created_at=datetime(2026, 5, 1, 14, 0, 0),
    )
    assert sm.source == "reddit"


def test_models_are_frozen():
    cp = CompanyProfile(ticker="AAPL", name="Apple", source="finnhub")
    with pytest.raises(ValidationError):
        cp.name = "Apple Inc."  # type: ignore[misc]
