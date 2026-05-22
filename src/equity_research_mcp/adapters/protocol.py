"""The SourceAdapter Protocol.

All adapters (Finnhub, EDGAR, Reddit, future paid sources) implement
this. Methods for capabilities a source does not provide raise
SourceCapabilityError (a typed EquityResearchError). Tools dispatch to
the right adapter for their need.

Adapters return instances of the schemas in equity_research_mcp.schemas.
Raw source payloads do not leave the adapter layer.
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

    async def get_social_mentions(
        self,
        ticker: str,
        subreddits: list[str],
        start: date,
        end: date,
    ) -> list[SocialMention]: ...
