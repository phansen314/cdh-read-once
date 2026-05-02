from __future__ import annotations

import os

import pytest

from cdh_read_once.cache import ReadOnceCache
from cdh_read_once.config import Settings
from cdh_read_once.handler import decide_read_once


def _settings(*, mode: str = "warn", disabled: bool = False, ttl_s: int = 1200) -> Settings:
    return Settings(
        bind_addr="127.0.0.1:9001",
        ttl_s=ttl_s,
        mode=mode,  # type: ignore[arg-type]
        disabled=disabled,
        cache_maxsize=10,
        max_concurrency=4,
        max_request_bytes=1_048_576,
    )


class _FakeStat:
    def __init__(self, mtime_ns: int = 100, size: int = 4096) -> None:
        self.st_mtime_ns = mtime_ns
        self.st_size = size


def _stat_factory(*, mtime_ns: int = 100, size: int = 4096):
    def stat(_path: str) -> os.stat_result:  # type: ignore[return-value]
        return _FakeStat(mtime_ns=mtime_ns, size=size)  # type: ignore[return-value]
    return stat


def _read_payload(file_path: str = "/tmp/foo.py", session_id: str = "s1", **extra) -> dict:
    tool_input = {"file_path": file_path}
    tool_input.update(extra)
    return {"session_id": session_id, "tool_name": "Read", "tool_input": tool_input}


def test_non_read_tool_returns_none():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    payload = {"session_id": "s1", "tool_name": "Edit",
               "tool_input": {"file_path": "/x"}}
    assert decide_read_once(payload, _settings(), cache, _stat_factory()) is None


def test_offset_skipped():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    payload = _read_payload(offset=10)
    assert decide_read_once(payload, _settings(), cache, _stat_factory()) is None


def test_limit_skipped():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    payload = _read_payload(limit=5)
    assert decide_read_once(payload, _settings(), cache, _stat_factory()) is None


def test_missing_file_path_returns_none():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    payload = {"session_id": "s1", "tool_name": "Read", "tool_input": {}}
    assert decide_read_once(payload, _settings(), cache, _stat_factory()) is None


def test_missing_session_id_returns_none():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    assert decide_read_once(payload, _settings(), cache, _stat_factory()) is None


def test_oserror_from_stat_returns_none():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)

    def stat(_p: str):
        raise FileNotFoundError(_p)

    assert decide_read_once(_read_payload(), _settings(), cache, stat) is None


def test_disabled_returns_none():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    out = decide_read_once(_read_payload(), _settings(disabled=True), cache,
                           _stat_factory())
    assert out is None


def test_first_read_miss_then_hit_warn_mode():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    s = _settings(mode="warn")
    stat = _stat_factory()
    assert decide_read_once(_read_payload(), s, cache, stat) is None
    out = decide_read_once(_read_payload(), s, cache, stat)
    assert out is not None
    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "allow"
    assert "read-once" in inner["permissionDecisionReason"]
    assert "foo.py" in inner["permissionDecisionReason"]


def test_hit_in_deny_mode_returns_deny():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    s = _settings(mode="deny")
    stat = _stat_factory()
    decide_read_once(_read_payload(), s, cache, stat)
    out = decide_read_once(_read_payload(), s, cache, stat)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_reason_mentions_ttl_minutes():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    s = _settings(ttl_s=600)
    stat = _stat_factory()
    decide_read_once(_read_payload(), s, cache, stat)
    out = decide_read_once(_read_payload(), s, cache, stat)
    assert "10m" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_mtime_change_yields_miss_again():
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    s = _settings()
    decide_read_once(_read_payload(), s, cache, _stat_factory(mtime_ns=100))
    out = decide_read_once(_read_payload(), s, cache, _stat_factory(mtime_ns=200))
    assert out is None
    out2 = decide_read_once(_read_payload(), s, cache, _stat_factory(mtime_ns=200))
    assert out2 is not None


@pytest.mark.parametrize("bad_payload", [
    {},
    {"tool_name": "Read"},
    {"tool_name": "Read", "tool_input": {"file_path": ""}, "session_id": "s1"},
    {"tool_name": "Read", "tool_input": {"file_path": "/x"}, "session_id": ""},
])
def test_malformed_payloads_return_none(bad_payload):
    cache = ReadOnceCache(maxsize=10, ttl_s=60)
    assert decide_read_once(bad_payload, _settings(), cache, _stat_factory()) is None
