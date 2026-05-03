# cdh-read-once

Production CDHP handler that prevents redundant `Read` tool calls within a Claude Code session.

Tracks `(session_id, file_path) → mtime_ns` per session. On re-read of an unchanged file within TTL: returns an `allow` envelope with an advisory reason (warn mode) or a `deny` envelope (deny mode). On first read or file change, abstains (`envelope: null`).

The handler **never reads file contents**. It only calls `os.stat()` to read `mtime_ns` and `st_size` (for a token-count estimate in the reason text). Nothing about file content is sent over the wire.

## Install / run

```bash
uv sync
CDH_BIND_ADDR=127.0.0.1:9001 uv run cdh-read-once
```

Logs to stderr.

## Configuration

| Var | Default | Effect |
|---|---|---|
| `CDH_BIND_ADDR` | (required) | `host:port` to bind. IPv6 uses bracketed form, e.g. `[::1]:9001`. Non-loopback hosts log a warning at startup (handler has no auth). |
| `READ_ONCE_MODE` | `warn` | `warn` → `allow` envelope with rationale; `deny` → `deny` envelope |
| `READ_ONCE_TTL` | `1200` | seconds before a cache entry expires |
| `READ_ONCE_DISABLED` | unset | set to `1`, `true`, `yes`, or `on` (case-insensitive) to no-op (always abstain) |
| `READ_ONCE_CACHE_MAXSIZE` | `10000` | max `(session_id, file_path)` entries |
| `READ_ONCE_MAX_CONCURRENCY` | `16` | bounded server thread pool |
| `READ_ONCE_MAX_REQUEST_BYTES` | `1048576` | reject oversized requests with 413 |

## Wire it to the cdh router

`~/.config/cdh/config.toml`:

```toml
[[handler]]
name = "read_once"
url = "http://127.0.0.1:9001"
events = ["preToolUse"]
```

Then `cdh start` (or restart). `cdh list-handlers` confirms it's alive.

## systemd user unit

### Pre-req: free the port

If a manual `uv run cdh-read-once` is already on the configured port, stop it first — systemd won't be able to bind:

```bash
ss -tlnp | grep ':9001 '          # find any listener
pkill -f 'cdh-read-once'          # or kill the specific PID
```

### Install

`~/.config/systemd/user/cdh-read-once.service`:

```ini
[Unit]
Description=cdh handler — read_once
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/code/cdh-read-once
Environment=CDH_BIND_ADDR=127.0.0.1:9001
ExecStart=%h/code/cdh-read-once/.venv/bin/cdh-read-once
Restart=on-failure
RestartSec=2

# User-mode-safe hardening. Stricter directives (PrivateDevices,
# RestrictAddressFamilies, ProtectKernel*, LockPersonality) require
# CAP_SYS_ADMIN and only work under system-mode systemd.
NoNewPrivileges=yes
PrivateTmp=yes
MemoryMax=128M
TasksMax=64

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now cdh-read-once
```

### Verify

```bash
systemctl --user is-active cdh-read-once          # → active
curl -s http://127.0.0.1:9001/health | jq .       # → name, version, cache_size
```

### Tail logs

```bash
journalctl --user -u cdh-read-once -f
```

### Uninstall

```bash
systemctl --user disable --now cdh-read-once
rm ~/.config/systemd/user/cdh-read-once.service
systemctl --user daemon-reload
```

### Stronger hardening (system-mode only)

If you run the handler as a *system* unit (root-owned, in `/etc/systemd/system/`), you can add the directives that need `CAP_SYS_ADMIN`:

```ini
ProtectSystem=strict
ProtectHome=read-only
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=yes
```

Under user-mode systemd these directives fail with `status=218/CAPABILITIES`.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

## Wire contract

Implements [CDHP 1.0](../claude-dynamic-hooks/docs/CDHP.md):

- `GET /health` → `{name, version, protocol_version, events, uptime_s, cache_size}`
- `POST /hooks/preToolUse` request `{"payload": "<json-string>"}` → `{"envelope": "<json-string>" | null}`
- `POST /admin/clear` → `{"cleared": true}`. Wipes the cache (escape hatch for `deny` mode).
- Other paths → 404. Bad request body → 400. Oversized → 413.
