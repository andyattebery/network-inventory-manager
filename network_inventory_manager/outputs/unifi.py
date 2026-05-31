from __future__ import annotations

import logging

import requests

from network_inventory_manager._types import DesiredState, normalize_mac
from network_inventory_manager.api.unifi import UnifiAPIClient

logger = logging.getLogger(__name__)


class UnifiOutput:
    def __init__(self, client: UnifiAPIClient) -> None:
        self._client = client

    def sync(self, desired: DesiredState, site: str, dry_run: bool) -> None:
        current_users = self._client.get_configured_users(site)
        current_by_mac: dict[str, dict] = {}
        for user in current_users:
            mac = user.get("mac")
            if mac:
                current_by_mac[normalize_mac(mac)] = user

        created = updated = unchanged = errors = 0
        for res in desired.dhcp_reservations:
            mac = normalize_mac(res.mac)
            existing = current_by_mac.get(mac)

            if existing:
                if (
                    existing.get("use_fixedip")
                    and existing.get("fixed_ip") == res.ip
                ):
                    logger.debug(
                        "Unchanged DHCP reservation: %s, %s, %s",
                        res.name, mac, res.ip,
                    )
                    unchanged += 1
                    continue

                existing_name = existing.get("name") or existing.get("hostname") or "(unnamed)"
                changes = []
                if existing_name != res.name:
                    changes.append(f"name: {existing_name} → {res.name}")
                if not existing.get("use_fixedip"):
                    changes.append("use_fixedip: False → True")
                if existing.get("fixed_ip") != res.ip:
                    changes.append(f"ip: {existing.get('fixed_ip')} → {res.ip}")

                prefix = "[DRY RUN] " if dry_run else ""
                verb = "would update" if dry_run else "Updated"
                logger.info(
                    "%s%s DHCP reservation: %s, %s, %s | %s",
                    prefix, verb, res.name, mac, res.ip, ", ".join(changes),
                )

                if dry_run:
                    updated += 1
                    continue
                try:
                    self._client.update_user(site, existing["_id"], {
                        "name": res.name,
                        "use_fixedip": True,
                        "fixed_ip": res.ip,
                    })
                    updated += 1
                except requests.RequestException:
                    logger.error("Failed to update DHCP reservation %s", mac, exc_info=True)
                    errors += 1
            else:
                prefix = "[DRY RUN] " if dry_run else ""
                verb = "would create" if dry_run else "Created"
                logger.info(
                    "%s%s DHCP reservation: %s, %s, %s",
                    prefix, verb, res.name, mac, res.ip,
                )

                if dry_run:
                    created += 1
                    continue
                try:
                    self._client.create_user(site, {
                        "mac": mac,
                        "name": res.name,
                        "use_fixedip": True,
                        "fixed_ip": res.ip,
                    })
                    created += 1
                except requests.RequestException:
                    logger.error("Failed to create DHCP reservation %s", mac, exc_info=True)
                    errors += 1

        logger.info(
            "DHCP: created %d, updated %d, unchanged %d, errors %d",
            created, updated, unchanged, errors,
        )
