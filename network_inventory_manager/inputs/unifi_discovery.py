from __future__ import annotations

import logging

from network_inventory_manager._types import DiscoveredClient, normalize_mac
from network_inventory_manager.api.unifi import UnifiAPIClient

logger = logging.getLogger(__name__)


def fetch(client: UnifiAPIClient, site: str) -> list[DiscoveredClient]:
    active = client.get_active_clients(site)
    configured = client.get_configured_users(site)

    merged: dict[str, dict] = {}
    for record in configured:
        mac = record.get("mac")
        if mac:
            merged[normalize_mac(mac)] = record
    for record in active:
        mac = record.get("mac")
        if not mac:
            continue
        key = normalize_mac(mac)
        if key in merged:
            existing = merged[key]
            if record.get("ip"):
                existing["ip"] = record["ip"]
            if record.get("name") and not existing.get("name"):
                existing["name"] = record["name"]
        else:
            merged[key] = record

    results: list[DiscoveredClient] = []
    for mac, record in merged.items():
        name = record.get("name") or record.get("hostname")
        ip = record.get("ip")
        if name and ip:
            results.append(DiscoveredClient(name=name, ip=ip, mac=mac))

    logger.info("Discovered %d UniFi clients with name and IP", len(results))
    return results
