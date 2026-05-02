from __future__ import annotations

import json
import signal
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .cache import ReadOnceCache
from .config import Settings, load_from_env
from .handler import decide_read_once

_NAME = "read_once"
_PROTOCOL_VERSION = "1.0"
_EVENTS = ["preToolUse"]

_SEMAPHORE_ACQUIRE_TIMEOUT_S = 5.0


def _make_request_handler(
    settings: Settings,
    cache: ReadOnceCache,
    semaphore: threading.BoundedSemaphore,
    started_monotonic: float,
) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        server_version = f"cdh-read-once/{_PROTOCOL_VERSION}"
        sys_version = ""

        def do_GET(self) -> None:
            if not self._acquire():
                return
            try:
                if self.path == "/health":
                    self._send_json(HTTPStatus.OK, {
                        "name": _NAME,
                        "protocol_version": _PROTOCOL_VERSION,
                        "events": _EVENTS,
                        "uptime_s": int(time.monotonic() - started_monotonic),
                    })
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            finally:
                semaphore.release()

        def do_POST(self) -> None:
            if not self._acquire():
                return
            try:
                if self.path != "/hooks/preToolUse":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._handle_pre_tool_use()
            finally:
                semaphore.release()

        def _acquire(self) -> bool:
            if semaphore.acquire(timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S):
                return True
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "handler busy"},
                _release=False,
            )
            return False

        def _handle_pre_tool_use(self) -> None:
            length_str = self.headers.get("Content-Length")
            if length_str is None:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "Content-Length required"})
                return
            try:
                length = int(length_str)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "invalid Content-Length"})
                return
            if length < 0:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "invalid Content-Length"})
                return
            if length > settings.max_request_bytes:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                {"error": "request body too large"})
                return

            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "request body is not valid JSON"})
                return
            if not isinstance(body, dict):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "request body must be a JSON object"})
                return

            payload_str = body.get("payload")
            if not isinstance(payload_str, str):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "missing or invalid 'payload' string"})
                return
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "'payload' is not valid JSON"})
                return
            if not isinstance(payload, dict):
                self._send_json(HTTPStatus.BAD_REQUEST,
                                {"error": "'payload' must be a JSON object"})
                return

            envelope_obj = decide_read_once(payload, settings, cache)
            envelope_str = json.dumps(envelope_obj) if envelope_obj is not None else None
            self._send_json(HTTPStatus.OK, {"envelope": envelope_str})

        def _send_json(self, status: HTTPStatus, obj: Any, *, _release: bool = True) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            sys.stderr.write(f"{ts} {self.address_string()} {fmt % args}\n")
            sys.stderr.flush()

    return Handler


def build_server(settings: Settings) -> tuple[ThreadingHTTPServer, ReadOnceCache]:
    host, _, port_s = settings.bind_addr.rpartition(":")
    if not host or not port_s:
        raise ValueError(f"CDH_BIND_ADDR must be host:port, got {settings.bind_addr!r}")
    port = int(port_s)

    cache = ReadOnceCache(maxsize=settings.cache_maxsize, ttl_s=settings.ttl_s)
    semaphore = threading.BoundedSemaphore(settings.max_concurrency)
    started = time.monotonic()
    handler_cls = _make_request_handler(settings, cache, semaphore, started)

    server = ThreadingHTTPServer((host, port), handler_cls)
    server.daemon_threads = True
    return server, cache


def main() -> None:
    try:
        settings = load_from_env()
    except ValueError as e:
        sys.stderr.write(f"cdh-read-once: {e}\n")
        sys.exit(2)

    server, _cache = build_server(settings)

    def _shutdown(*_a: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    sys.stderr.write(
        f"cdh-read-once: listening on {settings.bind_addr} "
        f"(mode={settings.mode}, ttl={settings.ttl_s}s, "
        f"max_concurrency={settings.max_concurrency})\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
