from __future__ import annotations

import logging
import subprocess
import tempfile

import os

import requests
import yaml

from network_inventory_manager._types import (
    HostEntry,
    InventoryError,
    NetworkHostsInventory,
    OtherHost,
    ServiceEntry,
    has_unresolved_op_ref,
    is_valid_dns_name,
    is_valid_ip,
    is_valid_mac,
)

logger = logging.getLogger(__name__)

# `op inject` on the homelab inventory resolves ~25 references across ~16 distinct
# 1Password items. At a pessimistic 1s per item that is ~16s, so 60s leaves ~4x
# headroom for a slow API or a cold service-account handshake. It is also 3.3% of
# the 1800s production sync interval, so a wedged `op` can never stall more than
# one cycle — which is the whole point: subprocess.run with no timeout blocks the
# single sync thread forever while /health keeps returning 200.
_OP_INJECT_TIMEOUT_SECONDS = 60

_FETCH_TIMEOUT_SECONDS = 30


def load(
    *,
    local_config_path: str = "",
    config_repo: str = "",
    repo_config_path: str = "",
    config_branch: str = "main",
    github_token: str = "",
    op_service_account_token: str = "",
) -> NetworkHostsInventory:
    """Load, resolve and validate the host inventory.

    Raises InventoryError for every failure mode. It never returns a partially
    resolved inventory: an unresolved reference reaching the desired state would
    make every existing AdGuardHome entry look stale.
    """
    template_text = _read_template(
        local_config_path=local_config_path,
        config_repo=config_repo,
        repo_config_path=repo_config_path,
        config_branch=config_branch,
        github_token=github_token,
    )

    resolved_text = _resolve_template(template_text, op_service_account_token)

    try:
        raw = yaml.safe_load(resolved_text)
    except yaml.YAMLError as exc:
        raise InventoryError(f"Inventory is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise InventoryError(
            f"Inventory did not parse to a mapping (got {type(raw).__name__})"
        )

    # Collected rather than raised on first hit, so an operator fixes everything
    # in one pass instead of one problem per sync interval.
    errors: list[str] = []

    domain = raw.get("homelab_domain")
    if not is_valid_dns_name(domain):
        errors.append(f"homelab_domain is not a valid DNS name: {domain!r}")

    homelab_hosts: dict[str, HostEntry] = {}
    for name, entry in (raw.get("homelab_hosts") or {}).items():
        if not is_valid_dns_name(name):
            errors.append(f"homelab_hosts key is not a valid DNS name: {name!r}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"homelab_hosts.{name} is not a mapping: {entry!r}")
            continue
        ip = entry.get("ip")
        if not is_valid_ip(ip):
            # Fatal. AdGuardHome diffs rewrites on the (domain, answer) tuple, so
            # a bad answer invalidates the whole current set exactly like a bad
            # domain does.
            errors.append(f"homelab_hosts.{name}.ip is not a valid IP address: {ip!r}")
            continue
        mac = entry.get("mac")
        if mac is not None and not is_valid_mac(str(mac)):
            # NOT fatal, deliberately. outputs/unifi.py only creates and updates
            # reservations, never deletes, so a dropped MAC loses one DHCP
            # reservation and cannot destroy existing state.
            logger.warning(
                "homelab_hosts.%s.mac is not a valid MAC (%r); skipping DHCP reservation",
                name, mac,
            )
            mac = None
        homelab_hosts[name] = HostEntry(
            ip=ip, mac=mac, skip_dhcp=bool(entry.get("skip_dhcp"))
        )

    other_hosts: list[OtherHost] = []
    for i, entry in enumerate(raw.get("other_hosts") or []):
        entry = entry or {}
        hostname, ip = entry.get("hostname"), entry.get("ip")
        if not is_valid_dns_name(hostname):
            errors.append(
                f"other_hosts[{i}].hostname is not a valid DNS name: {hostname!r}"
            )
            continue
        if not is_valid_ip(ip):
            errors.append(f"other_hosts[{i}].ip is not a valid IP address: {ip!r}")
            continue
        other_hosts.append(OtherHost(hostname=hostname, ip=ip))

    services: dict[str, ServiceEntry] = {}
    for name, entry in (raw.get("services") or {}).items():
        hostname = (entry or {}).get("hostname")
        if not is_valid_dns_name(name):
            errors.append(f"services key is not a valid DNS name: {name!r}")
            continue
        if not is_valid_dns_name(hostname):
            errors.append(
                f"services.{name}.hostname is not a valid host name: {hostname!r}"
            )
            continue
        services[name] = ServiceEntry(hostname=hostname)

    if errors:
        raise InventoryError(
            f"Inventory failed validation ({len(errors)} problem(s)): "
            + "; ".join(errors)
        )

    logger.info(
        "Loaded inventory: domain=%s, %d hosts, %d other hosts, %d services",
        domain, len(homelab_hosts), len(other_hosts), len(services),
    )
    return NetworkHostsInventory(
        homelab_domain=domain,
        homelab_hosts=homelab_hosts,
        other_hosts=other_hosts,
        services=services,
    )


def _read_template(
    *,
    local_config_path: str,
    config_repo: str,
    repo_config_path: str,
    config_branch: str,
    github_token: str,
) -> str:
    if local_config_path:
        try:
            with open(local_config_path) as f:
                return f.read()
        except OSError as exc:
            raise InventoryError(
                f"Cannot read inventory file {local_config_path}: {exc}"
            ) from exc

    url = (
        f"https://raw.githubusercontent.com/{config_repo}/{config_branch}/{repo_config_path}"
    )
    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    try:
        resp = requests.get(url, headers=headers, timeout=_FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise InventoryError(f"Cannot fetch inventory from {url}: {exc}") from exc
    return resp.text


def _resolve_template(template_text: str, op_service_account_token: str) -> str:
    if not has_unresolved_op_ref(template_text):
        # Nothing to resolve. Lets NIM run against a plain YAML inventory with no
        # `op` binary present.
        logger.debug("No 1Password references in inventory, skipping op inject")
        return template_text

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml.tpl", delete_on_close=False
    ) as f:
        f.write(template_text)
        f.flush()
        env = None
        if op_service_account_token:
            env = {**os.environ, "OP_SERVICE_ACCOUNT_TOKEN": op_service_account_token}
        try:
            result = subprocess.run(
                ["op", "inject", "-i", f.name],
                capture_output=True,
                text=True,
                env=env,
                timeout=_OP_INJECT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise InventoryError(
                "1Password CLI ('op') not found on PATH. The inventory contains "
                "{{ op://... }} references that cannot be resolved without it. "
                "Under NixOS the wrapProgram step in nix/package.nix supplies it — "
                "check the package was built with that postFixup."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InventoryError(
                f"op inject timed out after {_OP_INJECT_TIMEOUT_SECONDS}s "
                "(1Password API unreachable or the op daemon is wedged)"
            ) from exc

    if result.returncode != 0:
        # result.stdout is deliberately not logged: on a partial failure it can
        # contain already-resolved secret values.
        raise InventoryError(
            f"op inject failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    if has_unresolved_op_ref(result.stdout):
        # Defence in depth. op inject is documented as atomic; this makes that
        # assumption checked rather than assumed.
        raise InventoryError(
            "op inject reported success but its output still contains unresolved "
            "{{ op://... }} references"
        )

    return result.stdout
