"""FastMCP server entrypoint.

Phase 2 adds the first three research tools (3 of the 6 frozen for
v0.1). The diagnostic `health` tool from Phase 1 remains and is not
counted.
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


# --- Research tools (3 of 6 for v0.1). Phase 2: Finnhub-backed. ---


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
