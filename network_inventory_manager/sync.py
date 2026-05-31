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
    Rewrite,
    Settings,
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

    for name, host in inventory.homelab_hosts.items():
        rewrites_by_domain[f"{name}.{inventory.homelab_domain}"] = host.ip
        if host.ip not in clients_by_ip:
            clients_by_ip[host.ip] = name

    for other in inventory.other_hosts:
        rewrites_by_domain[other.hostname] = other.ip
        if other.ip not in clients_by_ip:
            clients_by_ip[other.ip] = other.hostname

    for service_name, service in inventory.services.items():
        domain = f"{service_name}.{inventory.homelab_domain}"
        if domain in rewrites_by_domain:
            continue
        host = inventory.homelab_hosts.get(service.hostname)
        if not host:
            logger.warning("Service %s references unknown host %s", service_name, service.hostname)
            continue
        rewrites_by_domain[domain] = host.ip

    for service in dsm_services:
        domain = urlparse(service.url).hostname
        if not domain or not domain.endswith(f".{inventory.homelab_domain}"):
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

    for dc in discovered_clients:
        if dc.ip not in clients_by_ip:
            clients_by_ip[dc.ip] = dc.name

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
    )


def run_sync(settings: Settings) -> None:
    inventory = network_hosts_inventory.load(
        local_config_path=settings.local_config_path,
        config_repo=settings.config_repo,
        repo_config_path=settings.repo_config_path,
        config_branch=settings.config_branch,
        github_token=settings.github_token,
        op_service_account_token=settings.op_service_account_token,
    )

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
        allow_removals = dsm_ok and discovery_ok

        if "adguardhome" in settings.outputs:
            try:
                AdGuardHomeOutput(
                    settings.adguardhome_url,
                    settings.adguardhome_username,
                    settings.adguardhome_password,
                ).sync(desired, settings.dry_run, allow_removals)
            except requests.RequestException:
                logger.error("AdGuardHome sync failed", exc_info=True)

        if "unifi" in settings.outputs and unifi is not None:
            try:
                UnifiOutput(unifi).sync(desired, settings.unifi_site, settings.dry_run)
            except requests.RequestException:
                logger.error("UniFi DHCP sync failed", exc_info=True)
