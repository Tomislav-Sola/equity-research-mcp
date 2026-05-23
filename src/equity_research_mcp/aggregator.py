"""research_brief aggregator — fans out over the four research adapters.

Concurrent fan-out via asyncio.gather under a single shared budget
context. Each section degrades gracefully on source-level errors (rate
limit, 4xx, parse failure, missing creds). BudgetExceeded does NOT
degrade: it is the brief's safety mechanism tripping (request cap
exceeded) and must be visible, so it propagates at the brief level
rather than being swallowed into one section's ok=False.

Correlation flags are co-occurrence-within-window booleans (see
schemas.ResearchBriefFlags for the full semantics note). Deterministic
only — no LLM at v0.1.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .budget import BUCKET_REQUESTS, budget_context
from .errors import BudgetExceeded
from .schemas import BriefSection, ResearchBrief, ResearchBriefFlags
from .tools import (
    _fetch_news,
    _fetch_profile,
    _fetch_price_action,
    _fetch_recent_filings,
)

# Worst-case fan-out budget. Sums per source:
#   profile (1) + price_action (1) + news (1) + filings (2 + N Form 4s).
# At ~20 Form 4s in a heavy-insider month the ceiling is ~25 calls.
# 80 is a RUNAWAY ceiling, not a generous typical-case cap — its job is
# to catch a brief that's burning requests on a misconfigured ticker or
# a loop. If a brief exhausts 80, the cap has correctly fired and the
# call fails loud (see the BudgetExceeded handling in research_brief).
BRIEF_LIMITS = {BUCKET_REQUESTS: 80}

# Volume z-score threshold for "abnormal" — under a normal distribution
# ~5% of trading days clear |z|>=2, the conventional volume-anomaly bar.
# The 30-bar rolling baseline has enough sample for population stdev
# (statistics.pstdev in tools._fetch_price_action) to be fine.
VOLUME_SPIKE_Z_THRESHOLD = 2.0

# Form types fetched by the brief. Form 4 carries parsed transactions;
# 8-K is metadata-only context for material events.
BRIEF_FILING_TYPES = ["4", "8-K"]


async def research_brief(ticker: str, days_back: int = 30) -> dict[str, Any]:
    """Fan out over the four research adapters, return a structured brief.

    Co-occurrence window semantics (IMPORTANT): the brief's flags label
    "insider transaction AND volume spike somewhere in the same
    days_back window," NOT same-day or near-in-time. An insider buy on
    day 2 and a volume spike on day 28 trip the buying flag exactly as
    both on day 5 — this is the v0.1 simplification, see
    ResearchBriefFlags.

    Source-level errors (rate limit, 4xx, parse failure, missing creds)
    degrade gracefully: the affected section returns ok=False, the rest
    of the brief stands. BudgetExceeded does NOT degrade — it's the
    safety mechanism on the brief itself and propagates so a runaway
    fan-out is visible rather than being swallowed as a phantom source
    outage.
    """
    end = date.today()
    start = end - timedelta(days=days_back)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    with budget_context(BRIEF_LIMITS):
        results = await asyncio.gather(
            _fetch_profile(ticker),
            _fetch_price_action(ticker, start_iso, end_iso),
            _fetch_news(ticker, start_iso, end_iso),
            _fetch_recent_filings(ticker, BRIEF_FILING_TYPES, days_back),
            return_exceptions=True,
        )

    # Change-of-policy from per-source degradation: BudgetExceeded is
    # not a source error, it's the brief's safety cap firing. Surface
    # it loud — partial data after the cap is misleading.
    for r in results:
        if isinstance(r, BudgetExceeded):
            raise r

    profile_r, price_r, news_r, filings_r = results
    profile_section = _section(profile_r)
    price_section = _section(price_r)
    news_section = _section(news_r)
    filings_section = _section(filings_r)

    flags = _compute_flags(price_section, filings_section)

    brief = ResearchBrief(
        ticker=ticker.upper(),
        days_back=days_back,
        start=start,
        end=end,
        generated_at=datetime.now(tz=UTC),
        profile=BriefSection(**profile_section),
        price_action=BriefSection(**price_section),
        news=BriefSection(**news_section),
        filings=BriefSection(**filings_section),
        flags=flags,
    )
    return brief.model_dump(mode="json")


def _section(result: Any) -> dict[str, Any]:
    """Wrap a gather result into a {ok, data, error} dict.

    Any BaseException becomes ok=False with a "Type: message" error
    string. The exception type is preserved verbatim so the consumer
    can distinguish RateLimited from MissingCredentials from a parse
    error. BudgetExceeded never reaches this helper — it's caught and
    re-raised in research_brief above.
    """
    if isinstance(result, BaseException):
        return {"ok": False, "data": None,
                "error": f"{type(result).__name__}: {result}"}
    return {"ok": True, "data": result, "error": None}


def _compute_flags(
    price_section: dict[str, Any],
    filings_section: dict[str, Any],
) -> ResearchBriefFlags:
    """Compute co-occurrence-within-window flags from two sections.

    "Co-occurrence within the window" means both conditions appeared
    somewhere in days_back — same-day or near-in-time proximity is NOT
    checked at v0.1 (see schemas.ResearchBriefFlags).

    Both flags are False when either input section is degraded — no
    data is treated as "no signal," never as a partial trip.
    """
    if not (price_section["ok"] and filings_section["ok"]):
        return ResearchBriefFlags(
            insider_buying_with_volume_spike=False,
            insider_selling_with_volume_spike=False,
        )

    bars = price_section["data"].get("bars", []) or []
    volume_spike_present = any(
        b.get("volume_zscore_30d") is not None
        and b["volume_zscore_30d"] >= VOLUME_SPIKE_Z_THRESHOLD
        for b in bars
    )

    filings = filings_section["data"].get("filings", []) or []
    has_open_market_buy = False
    has_open_market_sale = False
    for f in filings:
        if str(f.get("form_type", "")).upper() != "4":
            continue
        for tx in (f.get("transactions") or []):
            code = str(tx.get("transaction_code", "")).upper()
            ad = str(tx.get("acquired_or_disposed", "")).upper()
            if code == "P" and ad == "A":
                has_open_market_buy = True
            elif code == "S" and ad == "D":
                has_open_market_sale = True

    return ResearchBriefFlags(
        insider_buying_with_volume_spike=(
            has_open_market_buy and volume_spike_present
        ),
        insider_selling_with_volume_spike=(
            has_open_market_sale and volume_spike_present
        ),
    )
