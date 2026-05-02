from __future__ import annotations

import threading
from typing import Literal

from cachetools import TTLCache

CacheResult = Literal["hit", "miss"]


class ReadOnceCache:
    def __init__(self, *, maxsize: int, ttl_s: int) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_s)
        self._lock = threading.RLock()

    def check_and_update(
        self, session_id: str, file_path: str, mtime_ns: int,
    ) -> CacheResult:
        key = (session_id, file_path)
        with self._lock:
            cached = self._cache.get(key)
            if cached == mtime_ns:
                return "hit"
            self._cache[key] = mtime_ns
            return "miss"

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
