from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

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


def test_keys_differ_by_endpoint(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    cache.put("finnhub", "profile", {"t": "AAPL"}, "the-profile")
    cache.put("finnhub", "news", {"t": "AAPL"}, "the-news")
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, 60) == "the-profile"
    assert cache.get("finnhub", "news", {"t": "AAPL"}, 60) == "the-news"


def test_put_warns_and_does_not_raise_on_oserror(tmp_cache_dir):
    cache = FSCache(root=tmp_cache_dir)
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        with pytest.warns(RuntimeWarning, match="FSCache.put failed"):
            cache.put("finnhub", "profile", {"t": "AAPL"}, {"name": "Apple"})
    # Confirm nothing was persisted — pass-through, not partial write.
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, 60) is None


def test_init_warns_and_does_not_raise_when_mkdir_fails(tmp_path):
    bad_root = tmp_path / "cache"
    with patch("pathlib.Path.mkdir", side_effect=OSError("read-only fs")):
        with pytest.warns(RuntimeWarning, match="could not create"):
            cache = FSCache(root=bad_root)
    # And the cache still operates in pass-through mode without raising.
    assert cache.get("finnhub", "profile", {"t": "AAPL"}, 60) is None
