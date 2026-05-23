"""SEC EDGAR adapter — filings from the public submissions API.

Provides get_filings only. Form 4 (insider transactions) is parsed in
detail via the ownership-document XML; other form types (8-K, 13G, 13D)
return metadata. Form 4 parsing degrades gracefully: a single bad XML
yields a Filing with transactions=None and a stderr warning rather than
crashing the whole call.

SEC requires every request carry a User-Agent identifying the caller.
The adapter reads SEC_USER_AGENT at construction and sends it on every
HTTP call.
"""
from __future__ import annotations

import os
import warnings
import xml.etree.ElementTree as ET
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import httpx

from ..budget import BUCKET_REQUESTS, current
from ..cache import FSCache
from ..errors import (
    MissingCredentials,
    NotFound,
    RateLimited,
    SourceCapabilityError,
    UpstreamError,
)
from ..schemas import (
    CompanyProfile,
    Filing,
    InsiderTransaction,
    NewsItem,
    PriceBar,
    SocialMention,
)

SOURCE = "edgar"

EDGAR_DATA_BASE = "https://data.sec.gov"
EDGAR_FILES_BASE = "https://www.sec.gov"
EDGAR_ARCHIVES_PATH = "/Archives/edgar/data"
TICKERS_MAP_URL = f"{EDGAR_FILES_BASE}/files/company_tickers.json"

# Per-source TTLs. CLAUDE.md fixes filings=1w. Components:
TTL_TICKERS_MAP_SECONDS = 7 * 24 * 60 * 60   # ~static directory; refresh weekly
TTL_SUBMISSIONS_SECONDS = 24 * 60 * 60       # submissions index updates daily
TTL_FILING_XML_SECONDS = 7 * 24 * 60 * 60    # individual filings are immutable


class EdgarAdapter:
    name: str = SOURCE

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache: FSCache | None = None,
    ) -> None:
        ua = os.environ.get("SEC_USER_AGENT")
        if not ua:
            raise MissingCredentials("SEC_USER_AGENT")
        self._ua = ua
        if client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0, headers={"User-Agent": ua}
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False
        self._cache = cache or FSCache()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "EdgarAdapter":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _get(self, url: str) -> httpx.Response:
        current().charge(BUCKET_REQUESTS)
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise UpstreamError(SOURCE, f"network error: {exc}") from exc
        if response.status_code == 429:
            retry = response.headers.get("Retry-After")
            seconds = int(retry) if retry and retry.isdigit() else None
            raise RateLimited(SOURCE, retry_after_seconds=seconds)
        if response.status_code >= 400:
            raise UpstreamError(
                SOURCE, f"HTTP {response.status_code} from {url}"
            )
        return response

    async def _ticker_to_cik(self, ticker: str) -> str:
        """Resolve ticker → 10-digit zero-padded CIK string."""
        ticker_u = ticker.upper()
        cache_params: dict[str, Any] = {}
        cached = self._cache.get(
            SOURCE, "tickers_map", cache_params, TTL_TICKERS_MAP_SECONDS
        )
        if cached is not None:
            tmap = cached
        else:
            response = await self._get(TICKERS_MAP_URL)
            try:
                tmap = response.json()
            except ValueError as exc:
                raise UpstreamError(SOURCE, f"non-JSON tickers map: {exc}") from exc
            self._cache.put(SOURCE, "tickers_map", cache_params, tmap)
        for entry in tmap.values():
            if str(entry.get("ticker", "")).upper() == ticker_u:
                cik = entry.get("cik_str")
                if cik is None:
                    continue
                return f"{int(cik):010d}"
        raise NotFound(SOURCE, ticker_u)

    async def get_filings(
        self,
        ticker: str,
        form_types: list[str],
        start: date,
        end: date,
    ) -> list[Filing]:
        ticker_u = ticker.upper()
        cik = await self._ticker_to_cik(ticker_u)
        submissions = await self._submissions_for_cik(cik)

        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        wanted = {ft.upper() for ft in form_types}
        filings: list[Filing] = []
        for i, form_type in enumerate(forms):
            if str(form_type).upper() not in wanted:
                continue
            try:
                filed = date.fromisoformat(filing_dates[i])
                accession = accession_numbers[i]
                primary_doc = primary_docs[i]
            except (ValueError, IndexError, TypeError):
                # Skip rows where the parallel arrays disagree or the date
                # is malformed — surface what we can.
                continue
            if filed < start or filed > end:
                continue

            accession_clean = accession.replace("-", "")
            filing_dir = (
                f"{EDGAR_FILES_BASE}{EDGAR_ARCHIVES_PATH}"
                f"/{int(cik)}/{accession_clean}"
            )
            filing_url = f"{filing_dir}/{primary_doc}"

            transactions: list[InsiderTransaction] | None = None
            if str(form_type).upper() == "4":
                # SEC serves Form 4s as an XSL-styled HTML view at
                # primaryDocument (e.g. "xslF345X06/form4.xml"); the raw
                # ownership-document XML is the basename in the parent dir.
                # Filing.url stays the human-readable view; we fetch the
                # raw XML internally.
                xml_basename = primary_doc.rsplit("/", 1)[-1]
                xml_url = f"{filing_dir}/{xml_basename}"
                transactions = await self._parse_form4_transactions(xml_url)

            filings.append(
                Filing(
                    ticker=ticker_u,
                    form_type=str(form_type),
                    filed_at=datetime.combine(filed, time.min),
                    accession_number=accession,
                    url=filing_url,
                    transactions=transactions,
                    source=SOURCE,
                )
            )
        return filings

    async def _submissions_for_cik(self, cik: str) -> dict[str, Any]:
        cache_params = {"cik": cik}
        cached = self._cache.get(
            SOURCE, "submissions", cache_params, TTL_SUBMISSIONS_SECONDS
        )
        if cached is not None:
            return cached
        url = f"{EDGAR_DATA_BASE}/submissions/CIK{cik}.json"
        response = await self._get(url)
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamError(SOURCE, f"non-JSON submissions: {exc}") from exc
        self._cache.put(SOURCE, "submissions", cache_params, data)
        return data

    async def _parse_form4_transactions(
        self, xml_url: str
    ) -> list[InsiderTransaction] | None:
        """Fetch + parse Form 4 XML. Returns None on any failure (per-filing
        graceful degradation). RateLimited propagates — that's a stop signal.
        """
        cache_params = {"url": xml_url}
        cached = self._cache.get(
            SOURCE, "form4_xml", cache_params, TTL_FILING_XML_SECONDS
        )
        if cached is not None:
            xml_text = cached
        else:
            try:
                response = await self._get(xml_url)
            except RateLimited:
                raise  # rate limits are a stop-the-world signal
            except UpstreamError as exc:
                warnings.warn(
                    f"EdgarAdapter: failed to fetch Form 4 at {xml_url}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return None
            xml_text = response.text
            self._cache.put(SOURCE, "form4_xml", cache_params, xml_text)

        try:
            root = ET.fromstring(xml_text)
            return _extract_form4_transactions(root)
        except (ET.ParseError, ValueError, KeyError, TypeError) as exc:
            warnings.warn(
                f"EdgarAdapter: failed to parse Form 4 at {xml_url}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

    async def get_company_profile(self, ticker: str) -> CompanyProfile:
        raise SourceCapabilityError(SOURCE, "company_profile")

    async def get_price_bars(
        self, ticker: str, start: date, end: date
    ) -> list[PriceBar]:
        raise SourceCapabilityError(SOURCE, "price_bars")

    async def get_news(
        self, ticker: str, start: date, end: date
    ) -> list[NewsItem]:
        raise SourceCapabilityError(SOURCE, "news")

    async def get_social_mentions(
        self,
        ticker: str,
        subreddits: list[str],
        start: date,
        end: date,
    ) -> list[SocialMention]:
        raise SourceCapabilityError(SOURCE, "social_mentions")


def _extract_form4_transactions(root: ET.Element) -> list[InsiderTransaction]:
    """Walk a Form 4 ownershipDocument and pull nonDerivative transactions.

    Uses the first <reportingOwner>'s name + relationship for all
    transactions in the filing — joint filings are rare on Form 4, and
    the XML doesn't bind individual transactions to specific owners.
    Derivative transactions are skipped per Phase 3 scope.
    """
    owner_name = _xml_text(root, "./reportingOwner/reportingOwnerId/rptOwnerName")
    if not owner_name:
        raise ValueError("missing rptOwnerName")

    relationship_parts: list[str] = []
    rel = root.find("./reportingOwner/reportingOwnerRelationship")
    if rel is not None:
        if _xml_bool(rel, "isOfficer"):
            title = _xml_text(rel, "officerTitle")
            relationship_parts.append(f"Officer: {title}" if title else "Officer")
        if _xml_bool(rel, "isDirector"):
            relationship_parts.append("Director")
        if _xml_bool(rel, "isTenPercentOwner"):
            relationship_parts.append("10% Owner")
        if _xml_bool(rel, "isOther"):
            other = _xml_text(rel, "otherText")
            relationship_parts.append(f"Other: {other}" if other else "Other")
    insider_relationship = ", ".join(relationship_parts) or "Unspecified"

    out: list[InsiderTransaction] = []
    for tx in root.findall("./nonDerivativeTable/nonDerivativeTransaction"):
        try:
            tx_date_text = _xml_text(tx, "transactionDate/value")
            tx_code = _xml_text(tx, "transactionCoding/transactionCode") or ""
            shares_text = _xml_text(tx, "transactionAmounts/transactionShares/value")
            ad_code = _xml_text(
                tx, "transactionAmounts/transactionAcquiredDisposedCode/value"
            ) or ""
            ownership = _xml_text(
                tx, "ownershipNature/directOrIndirectOwnership/value"
            ) or ""
            if not tx_date_text or shares_text is None:
                raise ValueError("transaction missing date or shares")
            tx_date = date.fromisoformat(tx_date_text)
            # SEC stores share counts as decimals sometimes ("1000.0000").
            shares = int(Decimal(shares_text))
            price_text = _xml_text(
                tx, "transactionAmounts/transactionPricePerShare/value"
            )
            price = Decimal(price_text) if price_text else None
        except (ValueError, ArithmeticError) as exc:
            warnings.warn(
                f"EdgarAdapter: skipping malformed nonDerivativeTransaction: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        out.append(
            InsiderTransaction(
                insider_name=owner_name,
                insider_relationship=insider_relationship,
                transaction_code=tx_code,
                acquired_or_disposed=ad_code,
                transaction_date=tx_date,
                shares=shares,
                price=price,
                is_direct=(ownership == "D"),
            )
        )
    return out


def _xml_text(parent: ET.Element, path: str) -> str | None:
    el = parent.find(path)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _xml_bool(parent: ET.Element, path: str) -> bool:
    text = _xml_text(parent, path)
    return text in {"1", "true", "True"}
