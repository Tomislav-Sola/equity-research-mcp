"""Aggregator tests — mock the four _fetch_* helpers, assert on the brief.

Offline only. The four _fetch_* helpers in tools.py are the mock seam
(adapters have their own dedicated tests). Each test monkeypatches a
subset of them to canned async returns or async-raises, and asserts on
the brief shape, section ok/error states, flag firing, and the
BudgetExceeded-propagates-loud contract.
"""
from __future__ import annotations

from typing import Any

import pytest

from equity_research_mcp import aggregator
from equity_research_mcp.errors import (
    BudgetExceeded,
    MissingCredentials,
    RateLimited,
    UpstreamError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_profile() -> dict[str, Any]:
    return {
        "ticker": "ACME",
        "name": "Acme Corp",
        "exchange": "NASDAQ",
        "source": "finnhub",
    }


def _stub_news() -> dict[str, Any]:
    return {
        "ticker": "ACME",
        "start": "2026-04-23",
        "end": "2026-05-23",
        "items": [
            {"headline": "Acme reports Q1", "url": "https://example/n1",
             "published_at": "2026-05-10T12:00:00Z", "publisher": "Wire",
             "source": "finnhub", "ticker": "ACME"}
        ],
        "source": "finnhub",
    }


def _bars(z_values: list[float | None]) -> dict[str, Any]:
    """Build a price_action payload with the given per-bar z-scores."""
    bars = []
    for i, z in enumerate(z_values):
        bars.append({
            "ticker": "ACME",
            "date": f"2026-05-{i + 1:02d}",
            "open": "100", "high": "101", "low": "99", "close": "100",
            "volume": 1000000,
            "source": "yfinance",
            "volume_zscore_30d": z,
        })
    return {
        "ticker": "ACME",
        "start": "2026-04-23",
        "end": "2026-05-23",
        "bars": bars,
        "source": "yfinance",
    }


def _filing(form_type: str, code: str | None = None,
            ad: str | None = None) -> dict[str, Any]:
    """Build a fixture filing. If form_type is '4' and code+ad are given,
    attach one InsiderTransaction with those codes."""
    f = {
        "ticker": "ACME",
        "form_type": form_type,
        "filed_at": "2026-05-15T00:00:00",
        "accession_number": f"000000000-26-{form_type}",
        "url": "https://sec.example/f",
        "transactions": None,
        "source": "edgar",
    }
    if form_type == "4" and code is not None:
        f["transactions"] = [{
            "insider_name": "TEST INSIDER",
            "insider_relationship": "Officer: Test",
            "transaction_code": code,
            "acquired_or_disposed": ad or "",
            "transaction_date": "2026-05-15",
            "shares": 1000,
            "price": "100",
            "is_direct": True,
        }]
    return f


def _filings_payload(filings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ticker": "ACME",
        "filing_types": ["4", "8-K"],
        "days_back": 30,
        "start": "2026-04-23",
        "end": "2026-05-23",
        "filings": filings,
        "source": "edgar",
    }


def patch_fetches(
    monkeypatch,
    *,
    profile=None,
    price=None,
    news=None,
    filings=None,
) -> None:
    """Monkeypatch the four _fetch_* helpers in aggregator's namespace.

    Each kwarg is either a value (canned return) or an Exception
    (will be raised). Default = a stub return for each.
    """
    async def _wrap(value):
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(
        aggregator, "_fetch_profile",
        lambda ticker: _wrap(profile if profile is not None else _stub_profile()),
    )
    monkeypatch.setattr(
        aggregator, "_fetch_price_action",
        lambda ticker, s, e: _wrap(price if price is not None else _bars([None, None, None])),
    )
    monkeypatch.setattr(
        aggregator, "_fetch_news",
        lambda ticker, s, e: _wrap(news if news is not None else _stub_news()),
    )
    monkeypatch.setattr(
        aggregator, "_fetch_recent_filings",
        lambda ticker, types, days_back: _wrap(
            filings if filings is not None else _filings_payload([])
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 — Happy path: all four sources succeed
# ---------------------------------------------------------------------------

async def test_happy_path_all_sources_ok(monkeypatch):
    patch_fetches(monkeypatch)
    brief = await aggregator.research_brief("ACME", days_back=30)

    assert brief["ticker"] == "ACME"
    assert brief["days_back"] == 30
    assert brief["profile"]["ok"] is True
    assert brief["price_action"]["ok"] is True
    assert brief["news"]["ok"] is True
    assert brief["filings"]["ok"] is True
    assert brief["profile"]["error"] is None
    assert brief["profile"]["data"]["name"] == "Acme Corp"
    # No buys, no spike -> both flags False
    assert brief["flags"]["insider_buying_with_volume_spike"] is False
    assert brief["flags"]["insider_selling_with_volume_spike"] is False


# ---------------------------------------------------------------------------
# Test 2 — Single-source degradation: news fails, rest OK
# ---------------------------------------------------------------------------

async def test_single_source_degradation(monkeypatch):
    patch_fetches(
        monkeypatch,
        news=RateLimited("finnhub", retry_after_seconds=60),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)

    assert brief["profile"]["ok"] is True
    assert brief["price_action"]["ok"] is True
    assert brief["filings"]["ok"] is True
    assert brief["news"]["ok"] is False
    assert "RateLimited" in brief["news"]["error"]
    assert "finnhub" in brief["news"]["error"]
    assert brief["news"]["data"] is None


# ---------------------------------------------------------------------------
# Test 3 — All four sources fail: brief still returns, doesn't raise
# ---------------------------------------------------------------------------

async def test_all_sources_degraded(monkeypatch):
    patch_fetches(
        monkeypatch,
        profile=MissingCredentials("FINNHUB_API_KEY"),
        price=UpstreamError("yfinance", "yahoo down"),
        news=UpstreamError("finnhub", "HTTP 500"),
        filings=UpstreamError("edgar", "HTTP 503"),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)

    for section_name in ("profile", "price_action", "news", "filings"):
        assert brief[section_name]["ok"] is False
        assert brief[section_name]["error"] is not None
        assert brief[section_name]["data"] is None
    # Degraded inputs -> flags False (no signal, never partial trip)
    assert brief["flags"]["insider_buying_with_volume_spike"] is False
    assert brief["flags"]["insider_selling_with_volume_spike"] is False


# ---------------------------------------------------------------------------
# Test 4 — Buying flag fires (P/A + z>=2.0)
# ---------------------------------------------------------------------------

async def test_buying_flag_fires(monkeypatch):
    patch_fetches(
        monkeypatch,
        price=_bars([None, 2.5, None, 1.0, None]),
        filings=_filings_payload([_filing("4", code="P", ad="A")]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)

    assert brief["flags"]["insider_buying_with_volume_spike"] is True
    assert brief["flags"]["insider_selling_with_volume_spike"] is False


# ---------------------------------------------------------------------------
# Test 5 — Buying flag does NOT fire — half-trigger variants
# ---------------------------------------------------------------------------

async def test_buying_flag_does_not_fire_no_volume_spike(monkeypatch):
    """Insider P/A buy present but no bar has z>=2.0."""
    patch_fetches(
        monkeypatch,
        price=_bars([1.5, 1.9, None]),
        filings=_filings_payload([_filing("4", code="P", ad="A")]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)
    assert brief["flags"]["insider_buying_with_volume_spike"] is False


async def test_buying_flag_does_not_fire_no_insider_buy(monkeypatch):
    """Volume spike present but no P/A insider transaction."""
    patch_fetches(
        monkeypatch,
        price=_bars([None, 2.5, None]),
        filings=_filings_payload([_filing("4", code="S", ad="D")]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)
    assert brief["flags"]["insider_buying_with_volume_spike"] is False
    # Selling flag DOES fire here (S/D + spike)
    assert brief["flags"]["insider_selling_with_volume_spike"] is True


# ---------------------------------------------------------------------------
# Test 6 — Selling flag fires (S/D + z>=2.0)
# ---------------------------------------------------------------------------

async def test_selling_flag_fires(monkeypatch):
    patch_fetches(
        monkeypatch,
        price=_bars([None, 2.0, 0.5]),
        filings=_filings_payload([_filing("4", code="S", ad="D")]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)

    assert brief["flags"]["insider_selling_with_volume_spike"] is True
    assert brief["flags"]["insider_buying_with_volume_spike"] is False


# ---------------------------------------------------------------------------
# Test 7 — Both flags can fire together (mixed insider activity + spike)
# ---------------------------------------------------------------------------

async def test_both_flags_fire_together(monkeypatch):
    patch_fetches(
        monkeypatch,
        price=_bars([None, 3.0, None]),
        filings=_filings_payload([
            _filing("4", code="P", ad="A"),
            _filing("4", code="S", ad="D"),
        ]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)
    assert brief["flags"]["insider_buying_with_volume_spike"] is True
    assert brief["flags"]["insider_selling_with_volume_spike"] is True


# ---------------------------------------------------------------------------
# Test 8 — Flags False when an input source is degraded
# ---------------------------------------------------------------------------

async def test_flags_false_when_filings_degraded(monkeypatch):
    """Even with a fat volume spike, no filings data -> no flag fires."""
    patch_fetches(
        monkeypatch,
        price=_bars([None, 4.0, None]),
        filings=UpstreamError("edgar", "HTTP 503"),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)
    assert brief["filings"]["ok"] is False
    assert brief["flags"]["insider_buying_with_volume_spike"] is False
    assert brief["flags"]["insider_selling_with_volume_spike"] is False


async def test_flags_false_when_price_degraded(monkeypatch):
    """Even with insider buys, no price data -> no flag fires."""
    patch_fetches(
        monkeypatch,
        price=UpstreamError("yfinance", "yahoo down"),
        filings=_filings_payload([_filing("4", code="P", ad="A")]),
    )
    brief = await aggregator.research_brief("ACME", days_back=30)
    assert brief["price_action"]["ok"] is False
    assert brief["flags"]["insider_buying_with_volume_spike"] is False


# ---------------------------------------------------------------------------
# Test 9 — BudgetExceeded propagates loud (Change 1 contract)
# ---------------------------------------------------------------------------

async def test_budget_exceeded_propagates_does_not_degrade(monkeypatch):
    """BudgetExceeded is the brief's safety cap firing — it must NOT
    be swallowed into a per-source ok=False. Raise it at brief level.
    """
    patch_fetches(
        monkeypatch,
        profile=BudgetExceeded("requests", limit=80, attempted=81),
    )
    with pytest.raises(BudgetExceeded) as exc_info:
        await aggregator.research_brief("ACME", days_back=30)
    assert exc_info.value.bucket == "requests"
    assert exc_info.value.limit == 80
    assert exc_info.value.attempted == 81


async def test_budget_exceeded_from_multiple_sources_still_raises(monkeypatch):
    """Even if multiple sources hit the cap (concurrent contention),
    one BudgetExceeded surfaces — the brief doesn't paper over it."""
    patch_fetches(
        monkeypatch,
        profile=BudgetExceeded("requests", 80, 81),
        news=BudgetExceeded("requests", 80, 82),
    )
    with pytest.raises(BudgetExceeded):
        await aggregator.research_brief("ACME", days_back=30)


# ---------------------------------------------------------------------------
# Test 10 — Brief structure shape
# ---------------------------------------------------------------------------

async def test_brief_structure(monkeypatch):
    patch_fetches(monkeypatch)
    brief = await aggregator.research_brief("acme", days_back=14)

    # Top-level keys
    expected_keys = {
        "ticker", "days_back", "start", "end", "generated_at",
        "profile", "price_action", "news", "filings", "flags",
    }
    assert set(brief.keys()) == expected_keys
    assert brief["ticker"] == "ACME"  # uppercased
    assert brief["days_back"] == 14
    # generated_at is an ISO string with timezone (UTC)
    assert "T" in brief["generated_at"]
    assert brief["generated_at"].endswith("Z") or "+00:00" in brief["generated_at"]
    # Each section has ok/data/error
    for section in ("profile", "price_action", "news", "filings"):
        assert set(brief[section].keys()) == {"ok", "data", "error"}
    # Flags structure
    assert set(brief["flags"].keys()) == {
        "insider_buying_with_volume_spike",
        "insider_selling_with_volume_spike",
    }
