"""Filesystem cache for adapter responses.

Keyed by SHA-256 of (source, endpoint, params). Per-source TTL passed
in by the caller. Not used by adapters in Phase 1.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "equity-research-mcp"


class FSCache:
    def __init__(self, root: Path = DEFAULT_CACHE_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, source: str, endpoint: str, params: dict[str, Any]) -> str:
        normalized = json.dumps(
            {"source": source, "endpoint": endpoint, "params": params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(
        self,
        source: str,
        endpoint: str,
        params: dict[str, Any],
        ttl_seconds: int,
    ) -> Any | None:
        path = self._path(self._key(source, endpoint, params))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - data.get("written_at", 0) > ttl_seconds:
            return None
        return data.get("payload")

    def put(
        self,
        source: str,
        endpoint: str,
        params: dict[str, Any],
        payload: Any,
    ) -> None:
        path = self._path(self._key(source, endpoint, params))
        path.write_text(
            json.dumps({"payload": payload, "written_at": time.time()}, default=str),
            encoding="utf-8",
        )
