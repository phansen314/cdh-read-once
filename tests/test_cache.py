from __future__ import annotations

import time

from cdh_read_once.cache import ReadOnceCache


def test_first_call_is_miss():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    assert c.check_and_update("s1", "/a", 1) == "miss"


def test_second_same_call_is_hit():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    c.check_and_update("s1", "/a", 1)
    assert c.check_and_update("s1", "/a", 1) == "hit"


def test_changed_mtime_is_miss_and_replaces():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    c.check_and_update("s1", "/a", 1)
    assert c.check_and_update("s1", "/a", 2) == "miss"
    assert c.check_and_update("s1", "/a", 2) == "hit"


def test_different_session_independent():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    c.check_and_update("s1", "/a", 1)
    assert c.check_and_update("s2", "/a", 1) == "miss"


def test_different_path_independent():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    c.check_and_update("s1", "/a", 1)
    assert c.check_and_update("s1", "/b", 1) == "miss"


def test_ttl_expiry():
    c = ReadOnceCache(maxsize=10, ttl_s=1)
    c.check_and_update("s1", "/a", 1)
    time.sleep(1.1)
    assert c.check_and_update("s1", "/a", 1) == "miss"


def test_clear():
    c = ReadOnceCache(maxsize=10, ttl_s=60)
    c.check_and_update("s1", "/a", 1)
    c.clear()
    assert len(c) == 0
    assert c.check_and_update("s1", "/a", 1) == "miss"
