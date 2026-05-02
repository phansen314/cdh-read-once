from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath
from typing import Callable

from .cache import ReadOnceCache
from .config import Settings
from .envelope import build_pre_tool_use_envelope

_BYTES_PER_TOKEN_DIVISOR = 4
_TOKEN_OVERHEAD_FACTOR = 1.7

_log = logging.getLogger("cdh_read_once.handler")

StatFn = Callable[[str], os.stat_result]


def _estimate_tokens(file_size: int) -> int:
    return int(file_size / _BYTES_PER_TOKEN_DIVISOR * _TOKEN_OVERHEAD_FACTOR)


def decide_read_once(
    payload: dict,
    settings: Settings,
    cache: ReadOnceCache,
    stat: StatFn = os.stat,
) -> dict | None:
    if settings.disabled:
        return None
    if payload.get("tool_name") != "Read":
        return None

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    session_id = payload.get("session_id")
    if not isinstance(file_path, str) or not file_path:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None

    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return None

    try:
        st = stat(file_path)
    except OSError as e:
        _log.debug("stat failed for %s: %s", file_path, e)
        return None

    result = cache.check_and_update(session_id, file_path, st.st_mtime_ns)
    if result == "miss":
        return None

    name = PurePosixPath(file_path).name or file_path
    tokens = _estimate_tokens(st.st_size)
    ttl_minutes = max(1, settings.ttl_s // 60)
    reason = (
        f"read-once: {name} (~{tokens} tokens) already in context "
        f"(unchanged). Re-read allowed after {ttl_minutes}m or file change."
    )
    decision = "deny" if settings.mode == "deny" else "allow"
    return build_pre_tool_use_envelope(decision, reason)
