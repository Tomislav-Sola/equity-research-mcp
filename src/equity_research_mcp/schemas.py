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


class Filing(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    form_type: str
    filed_at: datetime
    accession_number: str
    url: str
    summary: str | None = None
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
    score: int
    num_comments: int
    created_at: datetime
    source: str = "reddit"
