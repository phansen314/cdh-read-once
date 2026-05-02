from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import PurePosixPath

from .cache import ReadOnceCache
from .config import Settings
from .envelope import build_pre_tool_use_envelope

_BYTES_PER_TOKEN = 4

_log = logging.getLogger("cdh_read_once.handler")

StatFn = Callable[[str], os.stat_result]


def _estimate_tokens(file_size: int) -> int:
    return file_size // _BYTES_PER_TOKEN


def decide_read_once(
    payload: dict[str, object],
    settings: Settings,
    cache: ReadOnceCache,
    stat: StatFn = os.stat,
) -> dict[str, object] | None:
    if settings.disabled:
        return None
    if payload.get("tool_name") != "Read":
        return None

    tool_input_raw = payload.get("tool_input") or {}
    if not isinstance(tool_input_raw, dict):
        return None
    tool_input = tool_input_raw
    file_path = tool_input.get("file_path")
    session_id = payload.get("session_id")
    if not isinstance(file_path, str) or not file_path:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None

    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    if offset is not None and not isinstance(offset, int):
        return None
    if limit is not None and not isinstance(limit, int):
        return None

    try:
        st = stat(file_path)
    except OSError as e:
        _log.debug("stat failed for %s: %s", file_path, e)
        return None

    result = cache.check_and_update(
        session_id, file_path, st.st_mtime_ns, offset, limit,
    )
    if result == "miss":
        return None

    name = PurePosixPath(file_path).name or file_path
    tokens = _estimate_tokens(st.st_size)
    ttl_str = f"{settings.ttl_s}s" if settings.ttl_s < 60 else f"{settings.ttl_s // 60}m"
    range_hint = ""
    if offset is not None or limit is not None:
        range_hint = f" [offset={offset}, limit={limit}]"
    reason = (
        f"read-once: {name}{range_hint} (~{tokens} tokens) already in context "
        f"(unchanged). Re-read allowed after {ttl_str} or file change."
    )
    if settings.mode == "deny":
        _log.info("deny session=%s path=%s", session_id[:8], file_path)
        return build_pre_tool_use_envelope("deny", reason)
    return build_pre_tool_use_envelope("allow", reason)
