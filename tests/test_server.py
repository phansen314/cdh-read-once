from __future__ import annotations

import json
import threading

import httpx
import pytest

from cdh_read_once.config import Settings
from cdh_read_once.server import build_server


@pytest.fixture
def server():
    settings = Settings(
        bind_addr="127.0.0.1:0",
        ttl_s=60,
        mode="warn",
        disabled=False,
        cache_maxsize=100,
        max_concurrency=8,
        max_request_bytes=4096,
    )
    srv, _cache = build_server(settings)
    host, port = srv.server_address
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_health_ok(server):
    r = httpx.get(f"{server}/health", timeout=2)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "read_once"
    assert body["protocol_version"] == "1.0"
    assert body["events"] == ["preToolUse"]
    assert isinstance(body["uptime_s"], int)


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
