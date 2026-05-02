from __future__ import annotations

import json
import threading

import httpx
import pytest

from cdh_read_once.config import Settings
from cdh_read_once.server import build_server


def _make_settings(**overrides) -> Settings:
    base = dict(
        bind_addr="127.0.0.1:0",
        host="127.0.0.1",
        port=0,
        ttl_s=60,
        mode="warn",
        disabled=False,
        cache_maxsize=100,
        max_concurrency=8,
        max_request_bytes=4096,
    )
    if "bind_addr" in overrides and "host" not in overrides:
        from cdh_read_once.config import _parse_bind
        h, p = _parse_bind(overrides["bind_addr"])
        overrides["host"] = h
        overrides["port"] = p
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _start(settings: Settings):
    srv, _cache = build_server(settings)
    host, port = srv.server_address[:2]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    return srv, thread, base


@pytest.fixture
def server():
    srv, thread, base = _start(_make_settings())
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_health_ok(server):
    from cdh_read_once import __version__

    r = httpx.get(f"{server}/health", timeout=2)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "read_once"
    assert body["version"] == __version__
    assert body["protocol_version"] == "1.0"
    assert body["events"] == ["preToolUse"]
    assert isinstance(body["uptime_s"], int)
    assert body["cache_size"] == 0


def test_unknown_get_404(server):
    r = httpx.get(f"{server}/nope", timeout=2)
    assert r.status_code == 404


def test_unknown_post_404(server):
    r = httpx.post(f"{server}/hooks/sessionStart",
                   json={"payload": "{}"}, timeout=2)
    assert r.status_code == 404


def test_pre_tool_use_valid_returns_envelope_field(server, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hello world")
    payload = {
        "session_id": "s1",
        "tool_name": "Read",
        "tool_input": {"file_path": str(f)},
    }
    r = httpx.post(f"{server}/hooks/preToolUse",
                   json={"payload": json.dumps(payload)}, timeout=2)
    assert r.status_code == 200
    assert "envelope" in r.json()
    assert r.json()["envelope"] is None  # first read = miss

    r2 = httpx.post(f"{server}/hooks/preToolUse",
                    json={"payload": json.dumps(payload)}, timeout=2)
    assert r2.status_code == 200
    env_str = r2.json()["envelope"]
    assert isinstance(env_str, str)
    env = json.loads(env_str)
    assert env["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_missing_payload_400(server):
    r = httpx.post(f"{server}/hooks/preToolUse", json={}, timeout=2)
    assert r.status_code == 400


def test_non_string_payload_400(server):
    r = httpx.post(f"{server}/hooks/preToolUse",
                   json={"payload": {"x": 1}}, timeout=2)
    assert r.status_code == 400


def test_payload_not_json_string_400(server):
    r = httpx.post(f"{server}/hooks/preToolUse",
                   json={"payload": "not-json"}, timeout=2)
    assert r.status_code == 400


def test_request_body_not_object_400(server):
    r = httpx.post(f"{server}/hooks/preToolUse",
                   content=b"[]",
                   headers={"Content-Type": "application/json"}, timeout=2)
    assert r.status_code == 400


def test_oversized_413(server):
    big = "x" * 10_000
    r = httpx.post(f"{server}/hooks/preToolUse",
                   json={"payload": big}, timeout=2)
    assert r.status_code == 413


def test_deny_mode_envelope(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hello")
    srv, thread, base = _start(_make_settings(mode="deny"))
    try:
        payload = {"session_id": "s1", "tool_name": "Read",
                   "tool_input": {"file_path": str(f)}}
        body = {"payload": json.dumps(payload)}
        r1 = httpx.post(f"{base}/hooks/preToolUse", json=body, timeout=2)
        assert r1.json()["envelope"] is None
        r2 = httpx.post(f"{base}/hooks/preToolUse", json=body, timeout=2)
        env = json.loads(r2.json()["envelope"])
        inner = env["hookSpecificOutput"]
        assert inner["permissionDecision"] == "deny"
        assert inner["hookEventName"] == "PreToolUse"
        assert "read-once" in inner["permissionDecisionReason"]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_503_when_semaphore_exhausted(monkeypatch, tmp_path):
    import cdh_read_once.server as srv_mod

    monkeypatch.setattr(srv_mod, "_SEMAPHORE_ACQUIRE_TIMEOUT_S", 0.05)
    srv, thread, base = _start(_make_settings(max_concurrency=0))
    try:
        r = httpx.get(f"{base}/health", timeout=2)
        assert r.status_code == 503
        assert r.json()["error"] == "handler busy"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_two_sessions_independent_via_http(server, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    base_payload = {"tool_name": "Read", "tool_input": {"file_path": str(f)}}

    p1 = {**base_payload, "session_id": "sA"}
    p2 = {**base_payload, "session_id": "sB"}

    r1 = httpx.post(f"{server}/hooks/preToolUse",
                    json={"payload": json.dumps(p1)}, timeout=2)
    assert r1.json()["envelope"] is None

    r2 = httpx.post(f"{server}/hooks/preToolUse",
                    json={"payload": json.dumps(p2)}, timeout=2)
    assert r2.json()["envelope"] is None

    r1b = httpx.post(f"{server}/hooks/preToolUse",
                     json={"payload": json.dumps(p1)}, timeout=2)
    assert r1b.json()["envelope"] is not None


def test_admin_clear(server, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    payload = {"session_id": "s1", "tool_name": "Read",
               "tool_input": {"file_path": str(f)}}
    body = {"payload": json.dumps(payload)}

    httpx.post(f"{server}/hooks/preToolUse", json=body, timeout=2)
    r2 = httpx.post(f"{server}/hooks/preToolUse", json=body, timeout=2)
    assert r2.json()["envelope"] is not None

    rc = httpx.post(f"{server}/admin/clear", timeout=2)
    assert rc.status_code == 200
    assert rc.json() == {"cleared": True}

    r3 = httpx.post(f"{server}/hooks/preToolUse", json=body, timeout=2)
    assert r3.json()["envelope"] is None


def test_ipv6_bind():
    srv, thread, base = _start(_make_settings(bind_addr="[::1]:0"))
    try:
        r = httpx.get(f"{base}/health", timeout=2)
        assert r.status_code == 200
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)
