from __future__ import annotations

import pytest

from equity_research_mcp.errors import (
    BudgetExceeded,
    EquityResearchError,
    MissingCredentials,
    NotFound,
    RateLimited,
    SourceCapabilityError,
    UpstreamError,
)


def test_missing_credentials_carries_var_name():
    err = MissingCredentials("FINNHUB_API_KEY")
    assert err.var_name == "FINNHUB_API_KEY"
    assert "FINNHUB_API_KEY" in str(err)
    assert isinstance(err, EquityResearchError)


def test_rate_limited_with_retry_after():
    err = RateLimited("finnhub", retry_after_seconds=30)
    assert err.source == "finnhub"
    assert err.retry_after_seconds == 30
    assert "finnhub" in str(err)
    assert "30" in str(err)


def test_rate_limited_without_retry_after():
    err = RateLimited("edgar")
    assert err.source == "edgar"
    assert err.retry_after_seconds is None
    assert "edgar" in str(err)


def test_not_found_carries_source_and_query():
    err = NotFound("finnhub", "ZZZZ")
    assert err.source == "finnhub"
    assert err.query == "ZZZZ"
    assert "finnhub" in str(err)
    assert "ZZZZ" in str(err)


def test_upstream_error_carries_source_and_detail():
    err = UpstreamError("edgar", "500 Internal Server Error")
    assert err.source == "edgar"
    assert err.detail == "500 Internal Server Error"
    assert "edgar" in str(err)
    assert "500" in str(err)


def test_budget_exceeded_carries_bucket_limit_attempted():
    err = BudgetExceeded("requests", limit=10, attempted=11)
    assert err.bucket == "requests"
    assert err.limit == 10
    assert err.attempted == 11
    assert "requests" in str(err)
    assert "10" in str(err)
    assert "11" in str(err)


def test_source_capability_error_carries_source_and_capability():
    err = SourceCapabilityError("reddit", "price_bars")
    assert err.source == "reddit"
    assert err.capability == "price_bars"
    assert "reddit" in str(err)
    assert "price_bars" in str(err)


@pytest.mark.parametrize(
    "exc",
    [
        MissingCredentials("X"),
        RateLimited("finnhub"),
        NotFound("finnhub", "AAPL"),
        UpstreamError("finnhub", "boom"),
        BudgetExceeded("requests", 1, 2),
        SourceCapabilityError("reddit", "filings"),
    ],
)
def test_all_typed_errors_inherit_from_base(exc):
    assert isinstance(exc, EquityResearchError)
    assert isinstance(exc, Exception)
