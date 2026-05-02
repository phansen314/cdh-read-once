from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["warn", "deny"]

_DEFAULT_TTL_S = 1200
_DEFAULT_CACHE_MAXSIZE = 10_000
_DEFAULT_MAX_CONCURRENCY = 16
_DEFAULT_MAX_REQUEST_BYTES = 1_048_576  # 1 MiB; matches CDHP wire_max_bytes default


@dataclass(frozen=True, slots=True)
class Settings:
    bind_addr: str
    ttl_s: int
    mode: Mode
    disabled: bool
    cache_maxsize: int
    max_concurrency: int
    max_request_bytes: int


def _int_env(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e
    if not (min_value <= value <= max_value):
        raise ValueError(f"{name} out of range [{min_value},{max_value}]: {value}")
    return value


def load_from_env() -> Settings:
    bind = os.environ.get("CDH_BIND_ADDR")
    if not bind:
        raise ValueError("CDH_BIND_ADDR is required (host:port)")
    if ":" not in bind:
        raise ValueError(f"CDH_BIND_ADDR must be host:port, got {bind!r}")

    mode_raw = os.environ.get("READ_ONCE_MODE") or "warn"
    if mode_raw not in ("warn", "deny"):
        raise ValueError(f"READ_ONCE_MODE must be 'warn' or 'deny', got {mode_raw!r}")

    return Settings(
        bind_addr=bind,
        ttl_s=_int_env("READ_ONCE_TTL", _DEFAULT_TTL_S, min_value=1, max_value=86_400),
        mode=mode_raw,
        disabled=os.environ.get("READ_ONCE_DISABLED") == "1",
        cache_maxsize=_int_env(
            "READ_ONCE_CACHE_MAXSIZE", _DEFAULT_CACHE_MAXSIZE,
            min_value=1, max_value=1_000_000,
        ),
        max_concurrency=_int_env(
            "READ_ONCE_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY,
            min_value=1, max_value=1024,
        ),
        max_request_bytes=_int_env(
            "READ_ONCE_MAX_REQUEST_BYTES", _DEFAULT_MAX_REQUEST_BYTES,
            min_value=1024, max_value=67_108_864,
        ),
    )
