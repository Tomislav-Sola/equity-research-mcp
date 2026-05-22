from __future__ import annotations

import pytest

from equity_research_mcp.budget import (
    BUCKET_REQUESTS,
    budget_context,
    current,
)
from equity_research_mcp.errors import BudgetExceeded


def test_outside_context_raises():
    with pytest.raises(RuntimeError):
        current()


def test_charge_within_limit():
    with budget_context({BUCKET_REQUESTS: 5}) as b:
        b.charge(BUCKET_REQUESTS)
        b.charge(BUCKET_REQUESTS, 2)
        assert b.spent[BUCKET_REQUESTS] == 3
        assert b.remaining(BUCKET_REQUESTS) == 2


def test_charge_exceeds_limit():
    with budget_context({BUCKET_REQUESTS: 2}):
        b = current()
        b.charge(BUCKET_REQUESTS)
        b.charge(BUCKET_REQUESTS)
        with pytest.raises(BudgetExceeded) as exc:
            b.charge(BUCKET_REQUESTS)
        assert exc.value.bucket == BUCKET_REQUESTS
        assert exc.value.limit == 2
        assert exc.value.attempted == 3


def test_unknown_bucket_is_silent():
    with budget_context({BUCKET_REQUESTS: 1}) as b:
        b.charge("tokens", 1_000_000)
        assert "tokens" not in b.spent


def test_nested_contexts_are_independent():
    with budget_context({BUCKET_REQUESTS: 5}) as outer:
        outer.charge(BUCKET_REQUESTS, 3)
        with budget_context({BUCKET_REQUESTS: 1}) as inner:
            inner.charge(BUCKET_REQUESTS)
            with pytest.raises(BudgetExceeded):
                inner.charge(BUCKET_REQUESTS)
        assert outer.spent[BUCKET_REQUESTS] == 3
