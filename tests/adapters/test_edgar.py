"""Fixture-based tests for EdgarAdapter.

All HTTP is intercepted at the transport level with respx; no live network
calls are made. Fixtures are loaded from tests/fixtures/edgar/.

Fixture notes:
- company_tickers.json  — 4 entries; test ticker ACME has cik_str 1234567890.
- submissions_acme.json — 6 recent filings: Form 4 (2026-05-15), Form 4
  (2026-05-10), 8-K (2026-05-08), 13G (2026-04-30), 10-Q (2026-04-25),
  Form 4 (2026-02-01). All accession numbers and company names are
  anonymized (no real SEC data).
- form4_well_formed.xml — three nonDerivativeTransactions for DOE JANE A
  (Officer: Chief Financial Officer): P/A direct 10000 @ 175.50 on
  2026-05-15; S/D indirect 5000 @ 177.25 on 2026-05-15; G/D direct 1000
  price=None on 2026-05-14.
- form4_malformed.xml   — truncated XML that won't parse; triggers the
  graceful-degradation path.

No real fields from SEC were used. All company names, CIKs, accession
numbers, and transactions are fully anonymized.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from equity_research_mcp.adapters.edgar import EdgarAdapter, _extract_form4_transactions
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
from equity_research_mcp.schemas import Filing, InsiderTransaction

FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK1234567890.json"

# Form 4 XML URLs derived from fixture accession numbers + primaryDocument values.
# accession 0001181431-26-000111 → dashes removed → 000118143126000111
# cik_int = int("1234567890") = 1234567890 (used verbatim in path)
FORM4_URL_1 = (
    "https://www.sec.gov/Archives/edgar/data/1234567890"
    "/000118143126000111/wf-form4_174731111.xml"
)
FORM4_URL_2 = (
    "https://www.sec.gov/Archives/edgar/data/1234567890"
    "/000118143126000112/wf-form4_174731112.xml"
)
FORM4_URL_3 = (
    "https://www.sec.gov/Archives/edgar/data/1234567890"
    "/000118143126000050/wf-form4_174730050.xml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(name: str) -> object:
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_adapter(tmp_cache_dir: Path, monkeypatch) -> EdgarAdapter:
    """Construct an EdgarAdapter with test credentials and an isolated cache."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    return EdgarAdapter(cache=FSCache(root=tmp_cache_dir))


# ---------------------------------------------------------------------------
# Test 1 — Happy path: get_filings with Form 4 transactions
# ---------------------------------------------------------------------------

@respx.mock
async def test_happy_path_form4_transactions(tmp_cache_dir, monkeypatch):
    """Successful full round-trip: tickers map + submissions + three Form 4 XMLs.

    Asserts 3 Form 4 Filings are returned, all with transactions populated
    (3 per filing from the well-formed XML), and spot-checks the first
    filing's first transaction for all InsiderTransaction fields.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 10}):
        filings = await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert len(filings) == 3
    for filing in filings:
        assert isinstance(filing, Filing)
        assert filing.form_type == "4"
        assert filing.ticker == "ACME"
        assert filing.source == "edgar"
        assert filing.transactions is not None
        assert len(filing.transactions) == 3

    # Spot-check first filing's first transaction in full
    tx0 = filings[0].transactions[0]
    assert isinstance(tx0, InsiderTransaction)
    assert tx0.insider_name == "DOE JANE A"
    assert tx0.insider_relationship == "Officer: Chief Financial Officer"
    assert tx0.transaction_code == "P"
    assert tx0.acquired_or_disposed == "A"
    assert tx0.shares == 10000
    assert tx0.price == Decimal("175.50")
    assert tx0.is_direct is True


# ---------------------------------------------------------------------------
# Test 2 — Happy path: filtering by form_type returns only requested forms
# ---------------------------------------------------------------------------

@respx.mock
async def test_filter_by_form_type_8k_and_13g(tmp_cache_dir, monkeypatch):
    """Requesting 8-K and 13G returns only those 2 filings.

    transactions must be None on each (only Form 4 gets XML parsing).
    No HTTP call is made to any Form 4 XML URL.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))

    # Register Form 4 routes with assert_all_called=False so unused routes don't fail.
    form4_route_1 = respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=""))
    form4_route_2 = respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=""))
    form4_route_3 = respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=""))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 10}):
        filings = await adapter.get_filings("ACME", ["8-K", "13G"], date(2026, 1, 1), date(2026, 12, 31))

    assert len(filings) == 2
    form_types_returned = {f.form_type for f in filings}
    assert form_types_returned == {"8-K", "13G"}
    for filing in filings:
        assert filing.transactions is None

    # No Form 4 XML was fetched
    assert form4_route_1.call_count == 0
    assert form4_route_2.call_count == 0
    assert form4_route_3.call_count == 0


# ---------------------------------------------------------------------------
# Test 3 — Happy path: date range filters
# ---------------------------------------------------------------------------

@respx.mock
async def test_date_range_filter_may_2026(tmp_cache_dir, monkeypatch):
    """start=2026-05-01, end=2026-05-31 returns only the two May Form 4s.

    The February Form 4 (2026-02-01) must be excluded. XML is fetched only
    for the two in-range filings (FORM4_URL_1 and FORM4_URL_2).
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    route_1 = respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=well_formed_xml))
    route_2 = respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    route_3 = respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 10}):
        filings = await adapter.get_filings("ACME", ["4"], date(2026, 5, 1), date(2026, 5, 31))

    assert len(filings) == 2
    filed_dates = {f.filed_at.date() for f in filings}
    assert filed_dates == {date(2026, 5, 15), date(2026, 5, 10)}
    for filing in filings:
        assert filing.transactions is not None

    # The February Form 4 (URL_3) must NOT have been fetched
    assert route_1.call_count == 1
    assert route_2.call_count == 1
    assert route_3.call_count == 0


# ---------------------------------------------------------------------------
# Test 4 — Missing credentials
# ---------------------------------------------------------------------------

def test_missing_credentials_raises(monkeypatch):
    """Constructing EdgarAdapter without SEC_USER_AGENT raises MissingCredentials."""
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(MissingCredentials) as exc_info:
        EdgarAdapter()
    assert exc_info.value.var_name == "SEC_USER_AGENT"


# ---------------------------------------------------------------------------
# Test 5a — 429 → RateLimited with Retry-After header
# ---------------------------------------------------------------------------

@respx.mock
async def test_rate_limited_with_retry_after(tmp_cache_dir, monkeypatch):
    """HTTP 429 with Retry-After: 30 on the tickers map raises RateLimited(retry_after_seconds=30)."""
    respx.get(TICKERS_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(RateLimited) as exc_info:
        with budget_context({BUCKET_REQUESTS: 5}):
            await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.retry_after_seconds == 30


# ---------------------------------------------------------------------------
# Test 5b — 429 → RateLimited without Retry-After header
# ---------------------------------------------------------------------------

@respx.mock
async def test_rate_limited_without_retry_after(tmp_cache_dir, monkeypatch):
    """HTTP 429 without Retry-After header raises RateLimited(retry_after_seconds=None)."""
    respx.get(TICKERS_URL).mock(
        return_value=httpx.Response(429, json={})
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(RateLimited) as exc_info:
        with budget_context({BUCKET_REQUESTS: 5}):
            await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.retry_after_seconds is None


# ---------------------------------------------------------------------------
# Test 6 — Non-429 4xx → UpstreamError
# ---------------------------------------------------------------------------

@respx.mock
async def test_non_429_4xx_maps_to_upstream_error(tmp_cache_dir, monkeypatch):
    """HTTP 404 on the submissions endpoint raises UpstreamError with status code in detail.

    The tickers map must succeed first so the failure occurs on the submissions fetch.
    """
    tickers_payload = load_json("company_tickers.json")
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(404, json={}))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(UpstreamError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 5}):
            await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert "404" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 7 — Ticker not in company_tickers map → NotFound
# ---------------------------------------------------------------------------

@respx.mock
async def test_unknown_ticker_raises_not_found(tmp_cache_dir, monkeypatch):
    """Requesting a ticker absent from company_tickers.json raises NotFound."""
    tickers_payload = load_json("company_tickers.json")
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(NotFound) as exc_info:
        with budget_context({BUCKET_REQUESTS: 5}):
            await adapter.get_filings("ZZZZ", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.query == "ZZZZ"


# ---------------------------------------------------------------------------
# Test 8 — Form 4 XML parse failure → graceful degradation per-filing
# ---------------------------------------------------------------------------

@respx.mock
async def test_form4_parse_failure_degrades_gracefully(tmp_cache_dir, monkeypatch):
    """Malformed XML on first Form 4 yields transactions=None for that filing only.

    The other two Form 4s (well-formed) still have transactions populated.
    A RuntimeWarning matching 'failed to parse Form 4' is emitted.
    The overall get_filings call must NOT raise.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    malformed_xml = load_text("form4_malformed.xml")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    # First Form 4 URL returns malformed XML; others return well-formed.
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=malformed_xml))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.warns(RuntimeWarning, match="failed to parse Form 4"):
        with budget_context({BUCKET_REQUESTS: 10}):
            filings = await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert len(filings) == 3
    # First filing (most recent — 2026-05-15) used the malformed XML
    assert filings[0].filed_at.date() == date(2026, 5, 15)
    assert filings[0].transactions is None
    # Other two filings must have transactions
    assert filings[1].transactions is not None
    assert len(filings[1].transactions) == 3
    assert filings[2].transactions is not None
    assert len(filings[2].transactions) == 3


# ---------------------------------------------------------------------------
# Test 9 — Form 4 XML fetch 4xx → graceful degradation per-filing
# ---------------------------------------------------------------------------

@respx.mock
async def test_form4_fetch_4xx_degrades_gracefully(tmp_cache_dir, monkeypatch):
    """HTTP 404 on first Form 4 XML fetch yields transactions=None for that filing.

    A RuntimeWarning matching 'failed to fetch Form 4' must be emitted.
    The other two filings (well-formed) still have transactions populated.
    The overall get_filings call must NOT raise.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(404, json={}))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.warns(RuntimeWarning, match="failed to fetch Form 4"):
        with budget_context({BUCKET_REQUESTS: 10}):
            filings = await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert len(filings) == 3
    assert filings[0].filed_at.date() == date(2026, 5, 15)
    assert filings[0].transactions is None
    assert filings[1].transactions is not None
    assert len(filings[1].transactions) == 3
    assert filings[2].transactions is not None
    assert len(filings[2].transactions) == 3


# ---------------------------------------------------------------------------
# Test 10 — Form 4 XML fetch 429 → RateLimited propagates (stop the world)
# ---------------------------------------------------------------------------

@respx.mock
async def test_form4_fetch_429_propagates_rate_limited(tmp_cache_dir, monkeypatch):
    """HTTP 429 on a Form 4 XML fetch propagates RateLimited — the whole call aborts.

    This is "stop-the-world" behaviour: unlike 4xx errors which degrade
    gracefully, a 429 on the XML fetch re-raises and exits get_filings.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    # First Form 4 URL returns 429; subsequent ones would return well-formed but won't be reached.
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(429, json={}))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(RateLimited) as exc_info:
        with budget_context({BUCKET_REQUESTS: 10}):
            await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"


# ---------------------------------------------------------------------------
# Test 11 — Budget charged on miss, cache hit skips charge
# ---------------------------------------------------------------------------

@respx.mock
async def test_cache_hit_skips_budget_charge(tmp_cache_dir, monkeypatch):
    """Second call with budget=0 succeeds via cache; routes hit exactly once total.

    First call (budget=5): hits tickers map + submissions over the wire.
    Second call (budget=0): both are served from cache — no BudgetExceeded.
    Route call counts confirm each URL was fetched exactly once across both calls.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")

    tickers_route = respx.get(TICKERS_URL).mock(
        return_value=httpx.Response(200, json=tickers_payload)
    )
    submissions_route = respx.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=submissions_payload)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)

    # First call — charges budget and warms both caches
    with budget_context({BUCKET_REQUESTS: 5}) as budget:
        filings_first = await adapter.get_filings(
            "ACME", ["8-K"], date(2026, 1, 1), date(2026, 12, 31)
        )
        remaining_after_first = budget.remaining(BUCKET_REQUESTS)

    assert remaining_after_first < 5  # at least 2 HTTP calls: tickers + submissions

    # Second call — budget=0; must succeed entirely from cache (no BudgetExceeded)
    with budget_context({BUCKET_REQUESTS: 0}):
        filings_second = await adapter.get_filings(
            "ACME", ["8-K"], date(2026, 1, 1), date(2026, 12, 31)
        )

    # Both calls return the same 8-K filing
    assert len(filings_first) == 1
    assert len(filings_second) == 1
    assert filings_first[0].accession_number == filings_second[0].accession_number

    # Each network route was called exactly once (on the first pass)
    assert tickers_route.call_count == 1
    assert submissions_route.call_count == 1


# ---------------------------------------------------------------------------
# Test 12 — Budget exhausted
# ---------------------------------------------------------------------------

@respx.mock
async def test_budget_exhausted_raises_budget_exceeded(tmp_cache_dir, monkeypatch):
    """Budget of 0 with no pre-warmed cache raises BudgetExceeded immediately."""
    # Register the route but expect it never to be called (BudgetExceeded fires first)
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={}))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(BudgetExceeded):
        with budget_context({BUCKET_REQUESTS: 0}):
            await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))


# ---------------------------------------------------------------------------
# Test 13 — SourceCapabilityError for unsupported methods
# ---------------------------------------------------------------------------

async def test_get_company_profile_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_company_profile raises SourceCapabilityError(source='edgar', capability='company_profile')."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_company_profile("ACME")

    assert exc_info.value.source == "edgar"
    assert exc_info.value.capability == "company_profile"


async def test_get_price_bars_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_price_bars raises SourceCapabilityError(source='edgar', capability='price_bars')."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_price_bars("ACME", date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.capability == "price_bars"


async def test_get_news_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_news raises SourceCapabilityError(source='edgar', capability='news')."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_news("ACME", date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.capability == "news"


async def test_get_social_mentions_raises_source_capability_error(tmp_cache_dir, monkeypatch):
    """get_social_mentions raises SourceCapabilityError(source='edgar', capability='social_mentions')."""
    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with pytest.raises(SourceCapabilityError) as exc_info:
        with budget_context({BUCKET_REQUESTS: 1}):
            await adapter.get_social_mentions("ACME", ["wallstreetbets"], date(2026, 1, 1), date(2026, 12, 31))

    assert exc_info.value.source == "edgar"
    assert exc_info.value.capability == "social_mentions"


# ---------------------------------------------------------------------------
# Test 14 — InsiderTransaction normalization: price=None for gift (code G)
# ---------------------------------------------------------------------------

@respx.mock
async def test_gift_transaction_has_none_price(tmp_cache_dir, monkeypatch):
    """The gift transaction (code G, third in fixture) has price=None and correct fields.

    Also verifies transaction_date, shares, acquired_or_disposed, is_direct.
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 10}):
        filings = await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    # Gift is the third transaction in the first filing's well-formed XML
    gift_tx = filings[0].transactions[2]
    assert gift_tx.transaction_code == "G"
    assert gift_tx.price is None
    assert gift_tx.shares == 1000
    assert gift_tx.transaction_date == date(2026, 5, 14)
    assert gift_tx.acquired_or_disposed == "D"
    assert gift_tx.is_direct is True


# ---------------------------------------------------------------------------
# Test 15 — InsiderTransaction normalization: Decimal precision (no float drift)
# ---------------------------------------------------------------------------

@respx.mock
async def test_insider_transaction_decimal_precision(tmp_cache_dir, monkeypatch):
    """prices are exact Decimals — no float-representation drift.

    transactions[0].price must be exactly Decimal('175.50') and
    transactions[1].price must be exactly Decimal('177.25').
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(return_value=httpx.Response(200, json=submissions_payload))
    respx.get(FORM4_URL_1).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_2).mock(return_value=httpx.Response(200, text=well_formed_xml))
    respx.get(FORM4_URL_3).mock(return_value=httpx.Response(200, text=well_formed_xml))

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 10}):
        filings = await adapter.get_filings("ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31))

    txs = filings[0].transactions
    assert isinstance(txs[0].price, Decimal)
    assert txs[0].price == Decimal("175.50")
    assert isinstance(txs[1].price, Decimal)
    assert txs[1].price == Decimal("177.25")


# ---------------------------------------------------------------------------
# Additional: pure-function test for _extract_form4_transactions
# ---------------------------------------------------------------------------

def test_extract_form4_transactions_pure_function():
    """_extract_form4_transactions parses the well-formed XML directly without HTTP.

    This exercises the module-level helper in isolation, confirming:
    - 3 transactions are returned
    - insider_name and insider_relationship are correctly composed
    - is_direct True for 'D', False for 'I' ownership
    - price=None for the gift (no transactionPricePerShare element)
    """
    import xml.etree.ElementTree as ET

    xml_text = load_text("form4_well_formed.xml")
    root = ET.fromstring(xml_text)
    txs = _extract_form4_transactions(root)

    assert len(txs) == 3

    tx0 = txs[0]
    assert tx0.insider_name == "DOE JANE A"
    assert tx0.insider_relationship == "Officer: Chief Financial Officer"
    assert tx0.transaction_code == "P"
    assert tx0.acquired_or_disposed == "A"
    assert tx0.shares == 10000
    assert tx0.price == Decimal("175.50")
    assert tx0.is_direct is True
    assert tx0.transaction_date == date(2026, 5, 15)

    tx1 = txs[1]
    assert tx1.transaction_code == "S"
    assert tx1.acquired_or_disposed == "D"
    assert tx1.is_direct is False  # ownership value is 'I'
    assert tx1.price == Decimal("177.25")

    tx2 = txs[2]
    assert tx2.transaction_code == "G"
    assert tx2.price is None
    assert tx2.is_direct is True


# ---------------------------------------------------------------------------
# Test 21 — XSL-prefix stripping: real SEC Form 4 primaryDocument is the
# styled HTML view at "xslF345X06/form4.xml"; the raw XML lives in the
# parent dir without the xsl prefix. The adapter must fetch the raw XML.
# ---------------------------------------------------------------------------

@respx.mock
async def test_form4_xml_cache_hit_skips_budget_charge(tmp_cache_dir, monkeypatch):
    """The form4_xml cache tier (TTL_FILING_XML_SECONDS) is the most expensive
    per-call: one charge per Form 4 in the result window. A repeat call must
    serve every Form 4 XML from cache and charge zero budget for them.
    This complements test_cache_hit_skips_budget_charge (which only verifies
    the tickers_map and submissions tiers, via the 8-K path).
    """
    tickers_payload = load_json("company_tickers.json")
    submissions_payload = load_json("submissions_acme.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    tickers_route = respx.get(TICKERS_URL).mock(
        return_value=httpx.Response(200, json=tickers_payload)
    )
    submissions_route = respx.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=submissions_payload)
    )
    form4_route_1 = respx.get(FORM4_URL_1).mock(
        return_value=httpx.Response(200, text=well_formed_xml)
    )
    form4_route_2 = respx.get(FORM4_URL_2).mock(
        return_value=httpx.Response(200, text=well_formed_xml)
    )
    form4_route_3 = respx.get(FORM4_URL_3).mock(
        return_value=httpx.Response(200, text=well_formed_xml)
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)

    # First call: warms tickers + submissions + all 3 Form 4 XMLs.
    with budget_context({BUCKET_REQUESTS: 10}):
        first = await adapter.get_filings(
            "ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31)
        )

    # Second call with budget 0: every fetch must come from cache,
    # including the Form 4 XML tier (the expensive one).
    with budget_context({BUCKET_REQUESTS: 0}):
        second = await adapter.get_filings(
            "ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31)
        )

    assert len(first) == len(second) == 3
    # Every route was hit exactly once across BOTH calls — second call
    # never reached the network.
    assert tickers_route.call_count == 1
    assert submissions_route.call_count == 1
    assert form4_route_1.call_count == 1
    assert form4_route_2.call_count == 1
    assert form4_route_3.call_count == 1


@respx.mock
async def test_form4_xsl_prefix_stripped_to_fetch_raw_xml(tmp_cache_dir, monkeypatch):
    """When primaryDocument is 'xslF345X06/form4.xml' (SEC's modern
    styled-view convention), the adapter must fetch the raw XML at
    'form4.xml' in the parent dir (no xsl prefix), while still storing
    the human-readable styled-view URL on Filing.url for the caller.
    """
    tickers_payload = load_json("company_tickers.json")
    well_formed_xml = load_text("form4_well_formed.xml")

    # Build a one-row submissions response with the xsl-styled primaryDocument.
    submissions_with_xsl = {
        "cik": "1234567890",
        "name": "Acme Corp",
        "filings": {
            "recent": {
                "form": ["4"],
                "filingDate": ["2026-05-15"],
                "accessionNumber": ["0001140361-26-099999"],
                "primaryDocument": ["xslF345X06/form4.xml"],
            }
        },
    }

    filing_dir = (
        "https://www.sec.gov/Archives/edgar/data/1234567890/000114036126099999"
    )
    styled_url = f"{filing_dir}/xslF345X06/form4.xml"  # human view
    raw_xml_url = f"{filing_dir}/form4.xml"  # what the adapter must fetch

    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=tickers_payload))
    respx.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=submissions_with_xsl)
    )
    raw_route = respx.get(raw_xml_url).mock(
        return_value=httpx.Response(200, text=well_formed_xml)
    )
    # If the adapter accidentally fetches the styled URL, this would be hit.
    styled_route = respx.get(styled_url).mock(
        return_value=httpx.Response(200, text="<html>not xml</html>")
    )

    adapter = make_adapter(tmp_cache_dir, monkeypatch)
    with budget_context({BUCKET_REQUESTS: 5}):
        filings = await adapter.get_filings(
            "ACME", ["4"], date(2026, 1, 1), date(2026, 12, 31)
        )

    assert len(filings) == 1
    filing = filings[0]
    # Filing.url is the human-readable styled view, not the raw XML path.
    assert filing.url == styled_url
    # transactions populated → adapter fetched raw XML, not the styled HTML.
    assert filing.transactions is not None
    assert len(filing.transactions) == 3
    assert raw_route.call_count == 1
    assert styled_route.call_count == 0
