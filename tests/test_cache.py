from __future__ import annotations

import json
import time

from equity_research_mcp.cache import FSCache


def test_put_then_get_returns_payload(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    cache.put("finnhub", "profile", {"ticker": "AAPL"}, {"name": "Apple"})
    got = cache.get("finnhub", "profile", {"ticker": "AAPL"}, ttl_seconds=60)
    assert got == {"name": "Apple"}


def test_get_returns_none_when_missing(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    assert cache.get("finnhub", "profile", {"t": "MSFT"}, ttl_seconds=60) is None


def test_get_returns_none_when_expired(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    cache.put("finnhub", "profile", {"t": "AAPL"}, {"name": "Apple"})
    path = next(tmp_cache_dir.iterdir())
    data = json.loads(path.read_text())
    # Clearly expired: 100_000 seconds ago vs a 60-second TTL. Big
    # margin makes the intent unmistakable and survives slow CI clocks.
    data["written_at"] = time.time() - 100_000
    path.write_text(json.dumps(data))
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, ttl_seconds=60) is None


def test_keys_differ_by_params(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    cache.put("finnhub", "profile", {"t": "AAPL"}, "apple")
    cache.put("finnhub", "profile", {"t": "MSFT"}, "msft")
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, 60) == "apple"
    assert cache.get("finnhub", "profile", {"t": "MSFT"}, 60) == "msft"


def test_keys_differ_by_source(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    cache.put("finnhub", "profile", {"t": "AAPL"}, "from-finnhub")
    cache.put("polygon", "profile", {"t": "AAPL"}, "from-polygon")
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, 60) == "from-finnhub"
    assert cache.get("polygon", "profile", {"t": "AAPL"}, 60) == "from-polygon"
