# Development

## Project structure

```
network_inventory_manager/
  __main__.py              # entry point, CLI args, HTTP server (/sync /health /metrics), sync loop
  _types.py                # dataclasses, Settings, SyncStatus, TimeoutSession, validators
  sync.py                  # orchestration (run_sync) + desired state builder
  api/
    unifi.py               # UniFi API client (auth, CSRF, session management)
  inputs/
    network_hosts_inventory.py  # load + resolve YAML inventory via op inject
    dsm.py                      # fetch services from Dashboard Services Manager
    unifi_discovery.py          # fetch + merge active/configured UniFi clients
  outputs/
    adguardhome.py         # sync rewrites + clients to AdGuardHome
    unifi.py               # sync DHCP reservations to UniFi
```

Key design:
- `_types.py` holds all shared dataclasses — no circular imports
- `sync.py` imports everything; no other module imports from `sync.py`
- Output classes take connection params in `__init__`, desired state in `sync()`
- `UnifiAPIClient` is a context manager shared between input (discovery) and output (DHCP)

## Setup

```bash
pip install -e ".[test]"
```

## Running tests

```bash
docker build -t nim-test -f - . <<'DOCKERFILE'
FROM python:3.12-slim
COPY . /src
WORKDIR /src
RUN pip install --no-cache-dir -e ".[test]"
CMD ["pytest", "tests/", "-v"]
DOCKERFILE
docker run --rm nim-test
```

Tests use `unittest.mock` for the UniFi client and `responses` for AdGuardHome HTTP mocking. No external services needed.

## Building Docker

```bash
docker build -t network-inventory-manager .
```

The Dockerfile supports both `linux/amd64` and `linux/arm64`.

## CI

The GitHub Actions workflow (`.github/workflows/ci.yaml`) runs on push to `main` and pull requests:

1. **test** — install deps, run pytest
2. **docker** (on tags only) — build multi-arch image, push to `ghcr.io/andyattebery/network-inventory-manager`

Tag format: `v1.0.0` for releases, `v1.0.0-rc1` for pre-releases. Pre-release tags push a versioned image but don't promote `:latest`.

## Adding a new output

1. Create `network_inventory_manager/outputs/myoutput.py`
2. Add a class with `__init__` for connection params and a `sync(desired, ...)` method
3. Call it from `run_sync()` in `sync.py`, gated on `settings.outputs`
4. Add the output name to `_SETTINGS_ENV_MAP` and `Settings` if it needs config
5. Add tests in `tests/test_myoutput.py`

## Adding a new input

1. Create `network_inventory_manager/inputs/myinput.py` with a function that returns data
2. Call it from `run_sync()` in `sync.py`
3. If it feeds into `build_desired_state`, add a parameter and incorporate the data
4. Wrap the call in try/except and cache its last known-good contribution if the input *contributes to* what gets removed, so an outage protects those entries instead of freezing every source's removals. If the input *is* the desired state — as the host inventory is — a failure must abort the whole cycle instead: degrading to an empty desired state makes every existing entry look stale.
5. Set a timeout. Use `TimeoutSession` from `_types.py` for anything session-based, or pass `HTTP_TIMEOUT_SECONDS` explicitly. `requests` waits forever by default and there is one sync thread, so a single untimed call wedges every later cycle while `/health` keeps reporting the last good result.

## Removals

Three gates sit between "absent from the desired state" and "deleted", and a new output should honour all of them — `RemovalPolicy` in `_types.py` carries them together:

- **protected** — a currently-unreachable input's last known-good entries
- **ownership** — only entries NIM recorded creating, kept as a `! nim-owned` comment line in AdGuardHome's `user_rules` so NIM itself stays stateless
- **grace window** — `removal_grace_cycles` consecutive absences, counted in `SourceCache` in memory so a restart delays removals rather than performing them

Nothing refuses a sync outright. An earlier version bounded removal *volume* and refused the whole resource when exceeded, which meant a legitimately large cleanup wedged the service with no way for the running unit to clear it. Log loudly and proceed instead.
