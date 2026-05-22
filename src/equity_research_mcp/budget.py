"""Per-tool-call budget mechanism.

Each MCP tool call enters a fresh Budget context with named buckets.
Adapters call current().charge(BUCKET_REQUESTS) on each outbound HTTP
call. Exceeding a bucket's limit raises BudgetExceeded.

v0.1 enforces only the 'requests' bucket. v0.3 will add 'tokens' at the
LLM gateway. Same charge(bucket, amount) API — genuinely additive.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from .errors import BudgetExceeded

BUCKET_REQUESTS = "requests"
BUCKET_TOKENS = "tokens"  # reserved for v0.3


@dataclass
class Budget:
    limits: dict[str, int]
    spent: dict[str, int] = field(default_factory=dict)

    def charge(self, bucket: str, amount: int = 1) -> None:
        if bucket not in self.limits:
            # Unknown bucket = not budgeted here. Silently accept.
            return
        new_spent = self.spent.get(bucket, 0) + amount
        if new_spent > self.limits[bucket]:
            raise BudgetExceeded(
                bucket=bucket, limit=self.limits[bucket], attempted=new_spent
            )
        self.spent[bucket] = new_spent

    def remaining(self, bucket: str) -> int | None:
        if bucket not in self.limits:
            return None
        return self.limits[bucket] - self.spent.get(bucket, 0)


_current: ContextVar[Budget | None] = ContextVar("budget", default=None)


def current() -> Budget:
    b = _current.get()
    if b is None:
        raise RuntimeError(
            "No active budget. Wrap tool work in `with budget_context(...)`."
        )
    return b


@contextmanager
def budget_context(limits: dict[str, int]) -> Iterator[Budget]:
    b = Budget(limits=dict(limits))
    token = _current.set(b)
    try:
        yield b
    finally:
        _current.reset(token)
