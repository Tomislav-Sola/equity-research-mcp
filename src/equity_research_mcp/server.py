"""FastMCP server entrypoint.

Phase 5 adds research_brief, completing the 5 research tools frozen for
v0.1 (4 single-source tools + the aggregator). The diagnostic `health`
tool from Phase 1 remains and is not counted.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import tools as _tools

mcp = FastMCP("equity-research-mcp")


# Diagnostic tool — does NOT count toward the 6 research tools frozen
# for v0.1 (see CLAUDE.md "Tools surface frozen at 6"). Revisit at the
# v0.1.0 release: keep as a diagnostic, or remove if the 6 tools cover
# the smoke-test need.
@mcp.tool()
def health() -> dict[str, str]:
    """Return server status. No external calls."""
    return {"status": "ok", "version": __version__}


# --- Research tools (5 of 5 for v0.1). Finnhub + yfinance + EDGAR. ---


@mcp.tool()
async def get_company_profile(ticker: str) -> dict[str, Any]:
    """Basic company metadata (name, exchange, industry, market cap, shares)."""
    return await _tools.get_company_profile(ticker)


@mcp.tool()
async def get_price_action(ticker: str, start: str, end: str) -> dict[str, Any]:
    """Daily price bars over [start, end] (ISO dates) with volume z-score
    versus the prior 30-trading-day average. Dates are inclusive.
    """
    return await _tools.get_price_action(ticker, start, end)


@mcp.tool()
async def get_news(ticker: str, start: str, end: str) -> dict[str, Any]:
    """Company news headlines over [start, end] (ISO dates). Dates are inclusive."""
    return await _tools.get_news(ticker, start, end)


@mcp.tool()
async def get_recent_filings(
    ticker: str,
    filing_types: list[str],
    days_back: int = 30,
) -> dict[str, Any]:
    """SEC filings (Form 4, 8-K, 13G, 13D) for ticker filed in the last
    `days_back` calendar days. Form 4 filings include parsed insider
    transactions (insider name, shares, price, A/D direction) when XML
    parsing succeeds; if a single filing fails to parse, that filing
    returns with transactions=None and a stderr warning. days_back
    filters on filing date.
    """
    return await _tools.get_recent_filings(ticker, filing_types, days_back)


@mcp.tool()
async def research_brief(ticker: str, days_back: int = 30) -> dict[str, Any]:
    """Headline aggregator. Fans out over profile + price_action + news +
    recent_filings concurrently and returns a structured brief with
    deterministic correlation flags.

    Source-level errors (rate limit, 4xx, parse failure, missing creds)
    degrade gracefully per section (ok=False, error=...) and don't kill
    the brief. BudgetExceeded does NOT degrade — it's the brief's safety
    cap and propagates loud.

    Flags are co-occurrence-within-the-window booleans, NOT same-day or
    near-in-time correlation. See aggregator.py for the full semantics
    note and the z>=2.0 / open-market-P+A justifications.
    """
    return await _tools.research_brief(ticker, days_back)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
