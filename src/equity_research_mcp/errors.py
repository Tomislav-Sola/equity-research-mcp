"""Typed exceptions used across the project."""
from __future__ import annotations


class EquityResearchError(Exception):
    """Base class for all project exceptions."""


class MissingCredentials(EquityResearchError):
    def __init__(self, var_name: str):
        super().__init__(f"Missing required environment variable: {var_name}")
        self.var_name = var_name


class RateLimited(EquityResearchError):
    def __init__(self, source: str, retry_after_seconds: int | None = None):
        msg = f"Rate limited by {source}"
        if retry_after_seconds is not None:
            msg += f" (retry after {retry_after_seconds}s)"
        super().__init__(msg)
        self.source = source
        self.retry_after_seconds = retry_after_seconds


class NotFound(EquityResearchError):
    def __init__(self, source: str, query: str):
        super().__init__(f"{source}: not found for {query!r}")
        self.source = source
        self.query = query


class UpstreamError(EquityResearchError):
    def __init__(self, source: str, detail: str):
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


class BudgetExceeded(EquityResearchError):
    def __init__(self, bucket: str, limit: int, attempted: int):
        super().__init__(
            f"Budget exceeded for bucket {bucket!r}: "
            f"attempted {attempted}, limit {limit}"
        )
        self.bucket = bucket
        self.limit = limit
        self.attempted = attempted


class SourceCapabilityError(EquityResearchError):
    """The adapter implements the Protocol but the source genuinely
    doesn't provide this kind of data (e.g. Reddit has no price bars)."""

    def __init__(self, source: str, capability: str):
        super().__init__(f"{source!r} does not provide {capability!r}")
        self.source = source
        self.capability = capability
