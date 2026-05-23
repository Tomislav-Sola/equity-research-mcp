"""The SourceAdapter Protocol.

v0.1 adapters: Finnhub, yfinance, EDGAR. Methods for capabilities a
source does not provide raise SourceCapabilityError (a typed
EquityResearchError). Tools dispatch to the right adapter for their
need.

Adapters return instances of the schemas in equity_research_mcp.schemas.
Raw source payloads do not leave the adapter layer.

get_social_mentions is an extension seam (see below) — no v0.1 adapter
implements it. See CLAUDE.md "Social source dropped from v0.1" for the
rationale.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..schemas import (
    CompanyProfile,
    Filing,
    NewsItem,
    PriceBar,
    SocialMention,
)


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    async def get_company_profile(self, ticker: str) -> CompanyProfile: ...

    async def get_price_bars(
        self, ticker: str, start: date, end: date
    ) -> list[PriceBar]: ...

    async def get_news(
        self, ticker: str, start: date, end: date
    ) -> list[NewsItem]: ...

    async def get_filings(
        self,
        ticker: str,
        form_types: list[str],
        start: date,
        end: date,
    ) -> list[Filing]: ...

    # Extension seam — declared but not implemented by any v0.1 adapter.
    # Three source-tier checks failed during Phases 2–4 (Finnhub free
    # tier, Reddit account ban, StockTwits Cloudflare gate). The
    # capability is retained on the Protocol so a future adapter — a
    # Reddit app with registered API access, a paid StockTwits Partner
    # key, a Bluesky firehose client — can land without restructuring
    # the contract.
    #
    # The current signature is Reddit-shaped (subreddits + date range)
    # because that was the original Phase 4 target. It is preserved as
    # historical context; revise the signature (and SocialMention) when
    # the first real social adapter ships and informs what the realistic
    # shape actually is. Don't preemptively redesign for a future source
    # whose constraints aren't known yet.
    async def get_social_mentions(
        self,
        ticker: str,
        subreddits: list[str],
        start: date,
        end: date,
    ) -> list[SocialMention]: ...
