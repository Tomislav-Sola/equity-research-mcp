"""Shared schemas for normalized adapter output.

Adapters return instances of these models. Raw source payloads do not
leave the adapter layer.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CompanyProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    name: str
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: Decimal | None = None
    shares_outstanding: int | None = None
    float_shares: int | None = None
    country: str | None = None
    source: str


class InsiderTransaction(BaseModel):
    """A single nonDerivative transaction parsed from a Form 4 filing.

    Field semantics:
    - transaction_code: raw single-letter SEC code (P, S, A, D, M, G, ...);
      not expanded to human-readable here so the LLM / aggregator can
      interpret. P = open-market purchase, S = open-market sale, etc.
    - acquired_or_disposed: "A" (holdings increased) or "D" (decreased).
      Complementary to transaction_code — gives the one-bit direction.
    - is_direct: True if the insider holds shares directly, False if held
      indirectly (trust, family member, LLC).
    - price: None when the transaction has no per-share price (e.g. gifts).
    """

    model_config = ConfigDict(frozen=True)

    insider_name: str
    insider_relationship: str  # composed: "Officer: CFO, Director", etc.
    transaction_code: str
    acquired_or_disposed: str  # "A" or "D"
    transaction_date: date
    shares: int
    price: Decimal | None = None
    is_direct: bool


class Filing(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    form_type: str
    filed_at: datetime
    accession_number: str
    url: str
    summary: str | None = None
    # Populated only for Form 4 when XML parsing succeeds. None means
    # either "not a Form 4" or "parse failed" (graceful degradation —
    # the filing itself still surfaces).
    transactions: list[InsiderTransaction] | None = None
    source: str = "edgar"


class PriceBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str


class NewsItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    headline: str
    url: str
    published_at: datetime
    publisher: str | None = None
    summary: str | None = None
    source: str


class SocialMention(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    subreddit: str
    post_id: str
    title: str
    url: str
    # Reddit upvotes minus downvotes — a popularity signal, NOT sentiment.
    score: int
    num_comments: int
    created_at: datetime
    source: str = "reddit"


# ---------------------------------------------------------------------------
# research_brief output schemas (Phase 5)
# ---------------------------------------------------------------------------


class BriefSection(BaseModel):
    """One section of a research_brief — wraps a per-source tool output.

    Either ok=True with data populated, or ok=False with error populated.
    Errors here are *source* failures (rate limits, upstream errors,
    timeouts, missing creds for that source). BudgetExceeded is NOT a
    source failure — see aggregator.research_brief for that contract.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: dict | None = None
    error: str | None = None


class ResearchBriefFlags(BaseModel):
    """Deterministic cross-source flags computed from the brief data.

    Each flag is a "co-occurrence within the brief window" boolean:
    both signals were present somewhere in days_back, NOT same-day or
    near-in-time. A flag fires for an insider buy on day 2 and a volume
    spike on day 28 just as it does for both on day 5. This is a v0.1
    simplification — the boolean labels what happened in the window,
    not how tightly the events lined up. Temporal-proximity scoring is
    a future refinement.

    Flags are False when any input source needed to evaluate them is
    degraded (ok=False) — no data is treated as "no signal," never as
    a partial trip.
    """

    model_config = ConfigDict(frozen=True)

    insider_buying_with_volume_spike: bool
    insider_selling_with_volume_spike: bool


class ResearchBrief(BaseModel):
    """The full research_brief output."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    days_back: int
    start: date
    end: date
    generated_at: datetime
    profile: BriefSection
    price_action: BriefSection
    news: BriefSection
    filings: BriefSection
    flags: ResearchBriefFlags
