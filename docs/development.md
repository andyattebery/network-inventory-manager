# Development

## Project structure

```
network_inventory_manager/
  __main__.py              # entry point, CLI args, HTTP server, sync loop
  _types.py                # dataclasses, Settings, utility functions
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
4. Wrap the call in try/except and track success for `allow_removals` if the input affects what gets removed
