from __future__ import annotations

import logging
from contextlib import ExitStack
from urllib.parse import urlparse

import requests

from network_inventory_manager._types import (
    Client,
    NetworkHostsInventory,
    DHCPReservation,
    DesiredState,
    DiscoveredClient,
    DsmService,
    InventoryError,
    RemovalPolicy,
    Rewrite,
    Settings,
    SourceCache,
    SyncStatus,
    normalize_mac,
)
from network_inventory_manager.api.unifi import UnifiAPIClient
from network_inventory_manager.inputs import dsm, network_hosts_inventory, unifi_discovery
from network_inventory_manager.outputs.adguardhome import AdGuardHomeOutput
from network_inventory_manager.outputs.unifi import UnifiOutput

logger = logging.getLogger(__name__)


def build_desired_state(
    inventory: NetworkHostsInventory,
    dsm_services: list[DsmService],
    discovered_clients: list[DiscoveredClient],
) -> DesiredState:
    rewrites_by_domain: dict[str, str] = {}
    clients_by_ip: dict[str, str] = {}

    # AdGuardHome stores rewrite domains lowercased. Building the desired set in
    # any other case makes every (domain, answer) tuple mismatch current_set,
    # which is a total delete-and-recreate — the same blast radius as an
    # unresolved reference, with nothing to detect it.
    homelab_domain = inventory.homelab_domain.lower()

    for name, host in inventory.homelab_hosts.items():
        rewrites_by_domain[f"{name.lower()}.{homelab_domain}"] = host.ip
        if host.ip not in clients_by_ip:
            # Client names are display names, not DNS — keep them as written.
            clients_by_ip[host.ip] = name

    for other in inventory.other_hosts:
        rewrites_by_domain[other.hostname.lower()] = other.ip
        if other.ip not in clients_by_ip:
            clients_by_ip[other.ip] = other.hostname

    for service_name, service in inventory.services.items():
        domain = f"{service_name.lower()}.{homelab_domain}"
        if domain in rewrites_by_domain:
            continue
        host = inventory.homelab_hosts.get(service.hostname)
        if not host:
            logger.warning("Service %s references unknown host %s", service_name, service.hostname)
            continue
        rewrites_by_domain[domain] = host.ip

    dsm_rewrites: set[tuple[str, str]] = set()
    for service in dsm_services:
        domain = urlparse(service.url).hostname  # urlsplit already lowercases
        if not domain or not domain.endswith(f".{homelab_domain}"):
            continue
        if domain in rewrites_by_domain:
            continue
        host = inventory.homelab_hosts.get(service.hostname)
        if not host:
            logger.warning(
                "DSM service %s references unknown host %s",
                service.name, service.hostname,
            )
            continue
        rewrites_by_domain[domain] = host.ip
        dsm_rewrites.add((domain, host.ip))

    discovered_names: set[str] = set()
    for dc in discovered_clients:
        if dc.ip not in clients_by_ip:
            clients_by_ip[dc.ip] = dc.name
            discovered_names.add(dc.name)

    rewrites = [Rewrite(domain=d, answer=a) for d, a in rewrites_by_domain.items()]
    clients = [Client(name=n, ids=[ip]) for ip, n in clients_by_ip.items()]
    dhcp_reservations = [
        DHCPReservation(name=name, mac=normalize_mac(host.mac), ip=host.ip)
        for name, host in inventory.homelab_hosts.items()
        if host.mac is not None and not host.skip_dhcp
    ]

    return DesiredState(
        rewrites=rewrites,
        clients=clients,
        dhcp_reservations=dhcp_reservations,
        dsm_rewrites=dsm_rewrites,
        discovered_clients=discovered_names,
    )


def run_sync(
    settings: Settings,
    *,
    dry_run: bool = False,
    cache: SourceCache | None = None,
    status: SyncStatus | None = None,
) -> bool:
    """Run one sync cycle. Returns False if the cycle did not fully apply.

    `cache` carries each optional input's last known-good contribution between
    cycles; pass the same instance across the loop. A fresh one is created when
    omitted, which is correct for a one-shot run.
    """
    if cache is None:
        cache = SourceCache()

    try:
        inventory = network_hosts_inventory.load(
            local_config_path=settings.local_config_path,
            config_repo=settings.config_repo,
            repo_config_path=settings.repo_config_path,
            config_branch=settings.config_branch,
            github_token=settings.github_token,
            op_service_account_token=settings.op_service_account_token,
        )
    except InventoryError:
        # Deliberately NOT "proceed with allow_removals=False": with no inventory
        # there is no desired state, so every existing AdGuardHome entry would
        # look stale and one boolean would be all that stood between that and a
        # full wipe. Skip the cycle; the next one retries.
        logger.error("Inventory unavailable, skipping sync cycle entirely", exc_info=True)
        if status is not None:
            status.inventory_failures += 1
        return False

    ok = True
    dsm_ok = True
    try:
        dsm_services = dsm.fetch_services(settings.dsm_url)
    except requests.RequestException:
        logger.warning("DSM unreachable, proceeding without service discovery", exc_info=True)
        dsm_services = []
        dsm_ok = False

    discovered: list[DiscoveredClient] = []
    unifi: UnifiAPIClient | None = None
    discovery_ok = False

    with ExitStack() as stack:
        try:
            unifi = stack.enter_context(
                UnifiAPIClient(settings.unifi_url, settings.unifi_username, settings.unifi_password)
            )
            discovered = unifi_discovery.fetch(unifi, settings.unifi_site)
            discovery_ok = True
        except requests.RequestException:
            logger.warning("UniFi unreachable, skipping client discovery", exc_info=True)

        desired = build_desired_state(inventory, dsm_services, discovered)

        # Per-source scoping: an unreachable input gets its last known-good
        # contribution protected from removal, so removals stay enabled for every
        # other source. Only an input that has never succeeded — no cache to
        # protect — falls back to freezing removals globally.
        allow_removals = True
        protected_rewrites: set[tuple[str, str]] = set()
        protected_clients: set[str] = set()

        if dsm_ok:
            cache.dsm_rewrites = desired.dsm_rewrites
        elif cache.dsm_rewrites is not None:
            protected_rewrites = cache.dsm_rewrites
            logger.info("Protecting %d cached DSM rewrites from removal", len(protected_rewrites))
        else:
            logger.warning("DSM unreachable and never cached; blocking all removals")
            allow_removals = False

        if discovery_ok:
            cache.discovered_clients = desired.discovered_clients
        elif cache.discovered_clients is not None:
            protected_clients = cache.discovered_clients
            logger.info("Protecting %d cached discovered clients from removal", len(protected_clients))
        else:
            logger.warning("UniFi discovery unavailable and never cached; blocking all removals")
            allow_removals = False

        policy = RemovalPolicy(
            allow_removals=allow_removals,
            protected_rewrites=protected_rewrites,
            protected_clients=protected_clients,
            grace_cycles=settings.removal_grace_cycles,
            rewrite_absences=cache.rewrite_absences,
            client_absences=cache.client_absences,
            track_ownership=True,
        )

        if "adguardhome" in settings.outputs:
            try:
                AdGuardHomeOutput(
                    settings.adguardhome_url,
                    settings.adguardhome_username,
                    settings.adguardhome_password,
                ).sync(desired, dry_run, allow_removals, policy=policy, status=status)
            except requests.RequestException:
                logger.error("AdGuardHome sync failed", exc_info=True)
                ok = False

        if "unifi" in settings.outputs and unifi is not None:
            try:
                UnifiOutput(unifi).sync(desired, settings.unifi_site, dry_run)
            except requests.RequestException:
                logger.error("UniFi DHCP sync failed", exc_info=True)
                ok = False

    return ok
