from __future__ import annotations

import ipaddress
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

# (connect, read). requests defaults to waiting forever, and NIM runs a single
# sync thread — one hung call wedges every cycle after it while /health keeps
# reporting the last good result, which is the failure mode /health is least able
# to notice. 30s read is generous for a LAN service and still bounded.
HTTP_TIMEOUT_SECONDS = (5, 30)


class TimeoutSession(requests.Session):
    """A Session that always sends a timeout.

    Set here rather than at each call site so a newly added request cannot forget
    one. Callers may still pass an explicit `timeout=` to override.
    """

    def request(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


class SyncStatus:
    """Outcome of the most recent sync cycle, shared with the HTTP thread.

    The sync loop writes and the HTTP handler reads from another thread, so the
    fields are guarded rather than relying on attribute assignment being atomic.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_cycle_at: float | None = None
        self._last_success_at: float | None = None
        self._applied = False
        self._error: str | None = None
        self.rewrites_added = 0
        self.rewrites_removed = 0
        self.removals_deferred = 0
        self.inventory_failures = 0

    def record(self, *, applied: bool, error: str | None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._last_cycle_at = now
            self._applied = applied
            self._error = error
            if applied:
                self._last_success_at = now

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "last_cycle_at": self._last_cycle_at,
                "last_success": self._last_success_at,
                "last_cycle_applied": self._applied,
                "last_error": self._error,
            }

    def health(self, stale_after: float | None, now: float | None = None) -> tuple[bool, dict]:
        """(healthy, body). Unhealthy before the first cycle, after a failed one,
        or when the last cycle is older than `stale_after`.

        The staleness term is the one that matters: reporting only the last
        outcome leaves a *hung* cycle looking healthy forever, and a hang is the
        failure mode nothing else here detects.
        """
        now = time.time() if now is None else now
        body = self.snapshot()
        last = body["last_cycle_at"]
        if last is None:
            body["reason"] = "no sync cycle has completed yet"
            return False, body
        if stale_after is not None and (now - last) > stale_after:
            body["reason"] = (
                f"last cycle was {int(now - last)}s ago, over the {int(stale_after)}s limit"
            )
            return False, body
        if not body["last_cycle_applied"]:
            body["reason"] = body["last_error"] or "last cycle did not fully apply"
            return False, body
        return True, body


class InventoryError(Exception):
    """The host inventory could not be loaded, resolved, or validated.

    network_hosts_inventory.load() raises only this — transport, subprocess,
    YAML and validation failures are all wrapped, so callers have exactly one
    thing to catch.
    """


DEFAULT_REMOVAL_GRACE_CYCLES = 8


@dataclass
class HostEntry:
    ip: str
    mac: str | None = None
    skip_dhcp: bool = False


@dataclass
class OtherHost:
    hostname: str
    ip: str


@dataclass
class ServiceEntry:
    hostname: str


@dataclass
class NetworkHostsInventory:
    homelab_domain: str
    homelab_hosts: dict[str, HostEntry]
    other_hosts: list[OtherHost]
    services: dict[str, ServiceEntry] = None

    def __post_init__(self) -> None:
        if self.services is None:
            self.services = {}


@dataclass
class DsmService:
    name: str
    url: str
    hostname: str


@dataclass
class Rewrite:
    domain: str
    answer: str


@dataclass
class Client:
    name: str
    ids: list[str]


@dataclass
class DHCPReservation:
    name: str
    mac: str
    ip: str


@dataclass
class DesiredState:
    rewrites: list[Rewrite]
    clients: list[Client]
    dhcp_reservations: list[DHCPReservation]
    # Provenance, used to protect an unreachable input's entries from removal
    # instead of freezing all removals. Empty when the input contributed nothing.
    dsm_rewrites: set[tuple[str, str]] = field(default_factory=set)
    discovered_clients: set[str] = field(default_factory=set)


@dataclass
class SourceCache:
    """Per-cycle state carried between sync cycles. Deliberately in memory only.

    Losing any of it fails in the safe direction — an unreachable input stops
    being protected (so removals freeze instead) and absence counters reset (so
    removals are delayed). Nothing here needs to survive a restart, which is why
    NIM still writes no files.
    """

    # Last known-good contribution of each optional input. When an input is
    # unreachable its previous contribution is protected from removal rather than
    # freezing removals globally — a DSM outage must not stop NIM reconciling
    # inventory-derived entries. None means "never succeeded", the one case that
    # still falls back to freezing removals.
    dsm_rewrites: set[tuple[str, str]] | None = None
    discovered_clients: set[str] | None = None

    # Consecutive cycles each entry has been absent from the desired state.
    # Counting cycles rather than storing a first-seen timestamp is what makes a
    # restart delay removals instead of performing them.
    rewrite_absences: dict[tuple[str, str], int] = field(default_factory=dict)
    client_absences: dict[str, int] = field(default_factory=dict)


@dataclass
class RemovalPolicy:
    """Everything that decides whether an absent entry may be deleted."""

    allow_removals: bool = True
    # Last known-good entries of an input that is currently unreachable.
    protected_rewrites: set[tuple[str, str]] = field(default_factory=set)
    protected_clients: set[str] = field(default_factory=set)
    # Consecutive absences required before deletion. 0 disables the grace window.
    grace_cycles: int = 0
    # Mutated in place so counts carry across cycles; see SourceCache.
    rewrite_absences: dict[tuple[str, str], int] = field(default_factory=dict)
    client_absences: dict[str, int] = field(default_factory=dict)
    # Restrict removals to rewrites NIM recorded creating, so hand-made entries
    # survive. Off by default: a bare RemovalPolicy() should do nothing special.
    # run_sync turns it on.
    track_ownership: bool = False


@dataclass
class DiscoveredClient:
    name: str
    ip: str
    mac: str


_UNRESOLVED_OP_RE = re.compile(r"\{\{\s*op://")

# One or more dot-separated DNS labels. Deliberately permissive about TLDs (a
# homelab may use `lan` or `home`), strict about everything an unresolved, empty
# or mangled 1Password reference looks like: braces, slashes, colons, spaces,
# empty strings, leading/trailing dots and hyphens.
_DNS_NAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
_MAC_SEPARATORS_RE = re.compile(r"[:.-]")
_MAC_HEX_RE = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(mac: str) -> str:
    return mac.lower().replace("-", ":")


def has_unresolved_op_ref(value: object) -> bool:
    """True if value still contains a literal {{ op://... }} reference."""
    return isinstance(value, str) and bool(_UNRESOLVED_OP_RE.search(value))


def is_valid_mac(value: object) -> bool:
    """12 hex digits, with optional ':' / '-' / '.' separators."""
    if not isinstance(value, str):
        return False
    return bool(_MAC_HEX_RE.match(_MAC_SEPARATORS_RE.sub("", value.lower())))


def is_valid_dns_name(value: object) -> bool:
    # Unresolved op:// refs are rejected by _DNS_NAME_RE itself — '{' is in none
    # of its character classes — so no separate has_unresolved_op_ref() call.
    return isinstance(value, str) and bool(_DNS_NAME_RE.match(value))


def is_valid_ip(value: object) -> bool:
    if not isinstance(value, str):
        # A YAML scalar like `ip: 10.0` parses as a float, not a string.
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


_SETTINGS_ENV_MAP = {
    "op_service_account_token": "OP_SERVICE_ACCOUNT_TOKEN",
    "github_token": "GITHUB_TOKEN",
    "config_repo": "CONFIG_REPO",
    "repo_config_path": "CONFIG_PATH",
    "config_branch": "CONFIG_BRANCH",
    "local_config_path": "LOCAL_CONFIG_PATH",
    "dsm_url": "DSM_URL",
    "adguardhome_url": "ADGUARDHOME_URL",
    "adguardhome_username": "ADGUARDHOME_USERNAME",
    "adguardhome_password": "ADGUARDHOME_PASSWORD",
    "unifi_url": "UNIFI_URL",
    "unifi_username": "UNIFI_USERNAME",
    "unifi_password": "UNIFI_PASSWORD",
    "unifi_site": "UNIFI_SITE",
    "outputs": "OUTPUTS",
    "sync_interval": "SYNC_INTERVAL",
    "verbose": "VERBOSE",
    "port": "PORT",
    "removal_grace_cycles": "REMOVAL_GRACE_CYCLES",
}

_SETTINGS_REQUIRED = {
    "dsm_url",
    "adguardhome_url", "adguardhome_username", "adguardhome_password",
    "unifi_url", "unifi_username", "unifi_password",
}


@dataclass(frozen=True)
class Settings:
    dsm_url: str
    adguardhome_url: str
    adguardhome_username: str
    adguardhome_password: str
    unifi_url: str
    unifi_username: str
    unifi_password: str
    op_service_account_token: str = ""
    config_repo: str = ""
    repo_config_path: str = ""
    local_config_path: str = ""
    github_token: str = ""
    config_branch: str = "main"
    unifi_site: str = "default"
    outputs: tuple[str, ...] = ("adguardhome", "unifi")
    sync_interval: int = 0
    verbose: bool = False
    port: int = 8080
    # Consecutive cycles an entry must be absent from the desired state before it
    # is deleted. Stops a service stopped for debugging from losing its DNS.
    removal_grace_cycles: int = DEFAULT_REMOVAL_GRACE_CYCLES

    @classmethod
    def load(cls, settings_path: Path | None = None) -> Settings:
        file_values: dict[str, str] = {}
        if settings_path and settings_path.is_file():
            with open(settings_path) as f:
                raw = yaml.safe_load(f) or {}
            for key in _SETTINGS_ENV_MAP:
                if key in raw and raw[key] is not None:
                    file_values[key] = str(raw[key])

        merged: dict[str, str] = {}
        for field_name, env_key in _SETTINGS_ENV_MAP.items():
            env_val = os.environ.get(env_key)
            if env_val is not None:
                merged[field_name] = env_val
            elif field_name in file_values:
                merged[field_name] = file_values[field_name]

        missing = _SETTINGS_REQUIRED - set(merged)
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(sorted(missing))}")

        has_local = bool(merged.get("local_config_path"))
        has_repo = bool(merged.get("config_repo") and merged.get("repo_config_path"))
        if not has_local and not has_repo:
            raise ValueError("Must set either local_config_path or both config_repo + repo_config_path")

        outputs_raw = merged.get("outputs", "adguardhome,unifi")
        return cls(
            dsm_url=merged["dsm_url"],
            adguardhome_url=merged["adguardhome_url"],
            adguardhome_username=merged["adguardhome_username"],
            adguardhome_password=merged["adguardhome_password"],
            unifi_url=merged["unifi_url"],
            unifi_username=merged["unifi_username"],
            unifi_password=merged["unifi_password"],
            op_service_account_token=merged.get("op_service_account_token", ""),
            config_repo=merged.get("config_repo", ""),
            repo_config_path=merged.get("repo_config_path", ""),
            local_config_path=merged.get("local_config_path", ""),
            github_token=merged.get("github_token", ""),
            config_branch=merged.get("config_branch", "main"),
            unifi_site=merged.get("unifi_site", "default"),
            outputs=tuple(s.strip() for s in outputs_raw.split(",")),
            sync_interval=int(merged.get("sync_interval", "0")),
            verbose=merged.get("verbose", "false").lower() == "true",
            port=int(merged.get("port", "8080")),
            removal_grace_cycles=int(
                merged.get("removal_grace_cycles", str(DEFAULT_REMOVAL_GRACE_CYCLES))
            ),
        )
