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
uv sync
```

This creates `.venv` on the Python pinned in `.python-version`, with the `dev` dependency group (pytest, responses).

## Dependencies

Change them with uv, which edits `pyproject.toml` and re-locks:

```bash
uv add 'somepackage>=1.0'        # runtime
uv add --dev 'somepackage>=1.0'  # tests and tooling
```

Commit `uv.lock` with `pyproject.toml`. Every build installs from the lockfile — CI's test job, the Docker image and the nix package (through uv2nix) — and CI's `uv sync --locked` fails when the two disagree.

Dependabot (`.github/dependabot.yml`) opens one grouped pull request a month each for `uv.lock`, the `flake.lock` inputs and the workflow actions.

## Running tests

```bash
docker run --rm --volume .:/app --volume /app/.venv -w /app \
  ghcr.io/astral-sh/uv:0.13.4-python3.12-trixie-slim uv run --locked pytest tests -v
```

The anonymous `/app/.venv` volume keeps the container's environment out of the checkout ([uv's Docker guide](https://docs.astral.sh/uv/guides/integration/docker/#mounting-the-project-with-docker-run)). A `docker build`-based test image would not work: `.dockerignore` keeps `tests/` out of the build context. With Nix, `nix flake check` runs the suite as `checks.<system>.pytest`.

Tests use `unittest.mock` for the UniFi client and `responses` for AdGuardHome HTTP mocking. No external services needed.

## Building Docker

```bash
docker build -t network-inventory-manager .
```

The Dockerfile supports both `linux/amd64` and `linux/arm64`.

## CI

The GitHub Actions workflow (`.github/workflows/ci.yaml`) runs on push to `main`, on pull requests, on tags, and on `workflow_dispatch` with an existing tag (every job then checks out that tag, so a re-release is tested as it ships):

1. **test** — `uv sync --locked`, run pytest
2. **nix** (x86_64 and arm64 runners) — `nix flake check` (module import test, op-cli pin, the test suite against the installed package), then build the package
3. **docker** (on tags only) — check the tag matches `pyproject.toml` as a PEP 440 version, build the multi-arch image, push to `ghcr.io/andyattebery/network-inventory-manager`, promote `:latest` unless it is a pre-release
4. **github-release** (on tags only) — create the GitHub release
5. **release-branch** (on release tags only) — fast-forward the `release` branch to the tag, once test, nix and docker have passed

## Releasing

The version lives only in `pyproject.toml` (the nix package reads it through `uv.lock`), so it is bumped before tagging:

```bash
uv version --bump patch --no-sync    # or minor / major; rewrites pyproject.toml and uv.lock
git add pyproject.toml uv.lock
git commit -m "release $(uv version --short)"
git tag "$(uv version --short)"
git push origin main "$(uv version --short)"
```

The docker job fails a tag that names a different version. Consumers' flakes follow the `release` branch (`github:andyattebery/network-inventory-manager/release`), which only moves to a release tag whose own test, nix and docker jobs passed, so `nix flake update` picks up releases and nothing else.

A pre-release is whatever PEP 440 calls one: `uv version --bump minor --bump rc --no-sync` gives `0.3.0rc1`, tagged `0.3.0rc1` (another spelling of the same version, such as `v0.3.0-rc.1`, also matches). It gets a versioned image and a GitHub pre-release, but does not promote `:latest` or move `release`.

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
