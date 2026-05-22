from __future__ import annotations

import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache
