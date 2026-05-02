from __future__ import annotations

import threading
from typing import Literal

from cachetools import TTLCache

CacheResult = Literal["hit", "miss"]

CacheKey = tuple[str, str, int, int | None, int | None]


class ReadOnceCache:
    def __init__(self, *, maxsize: int, ttl_s: int) -> None:
        self._cache: TTLCache[CacheKey, bool] = TTLCache(maxsize=maxsize, ttl=ttl_s)
        self._lock = threading.Lock()

    def check_and_update(
        self,
        session_id: str,
        file_path: str,
        mtime_ns: int,
        offset: int | None = None,
        limit: int | None = None,
    ) -> CacheResult:
        key: CacheKey = (session_id, file_path, mtime_ns, offset, limit)
        with self._lock:
            if key in self._cache:
                return "hit"
            self._cache[key] = True
            return "miss"

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
