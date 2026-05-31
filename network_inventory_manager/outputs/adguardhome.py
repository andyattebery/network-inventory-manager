from __future__ import annotations

import logging

import requests

from network_inventory_manager._types import Client, DesiredState, Rewrite

logger = logging.getLogger(__name__)

_CLIENT_DEFAULTS = {
    "use_global_settings": True,
    "use_global_blocked_services": True,
    "filtering_enabled": False,
    "parental_enabled": False,
    "safebrowsing_enabled": False,
    "safe_search": {"enabled": False},
}


class AdGuardHomeOutput:
    def __init__(self, url: str, username: str, password: str) -> None:
        self._url = url
        self._session = requests.Session()
        self._session.auth = (username, password)

    def sync(self, desired: DesiredState, dry_run: bool, allow_removals: bool) -> None:
        self._sync_rewrites(desired.rewrites, dry_run, allow_removals)
        self._sync_clients(desired.clients, dry_run, allow_removals)

    def _sync_rewrites(
        self, desired: list[Rewrite], dry_run: bool, allow_removals: bool
    ) -> None:
        resp = self._session.get(f"{self._url}/control/rewrite/list")
        resp.raise_for_status()
        current_set = {(r["domain"], r["answer"]) for r in resp.json()}
        desired_set = {(r.domain, r.answer) for r in desired}

        to_add = desired_set - current_set
        to_remove = current_set - desired_set
        unchanged_set = current_set & desired_set

        added = removed = errors = 0
        for domain, answer in to_remove:
            if not allow_removals:
                logger.info("Skipping removal (input degraded): %s → %s", domain, answer)
                continue
            if dry_run:
                logger.info("[DRY RUN] AdGuardHome: would remove rewrite %s → %s", domain, answer)
                removed += 1
                continue
            try:
                r = self._session.post(
                    f"{self._url}/control/rewrite/delete",
                    json={"domain": domain, "answer": answer},
                )
                r.raise_for_status()
                logger.info("Removed rewrite %s → %s", domain, answer)
                removed += 1
            except requests.RequestException:
                logger.error("Failed to remove rewrite %s → %s", domain, answer, exc_info=True)
                errors += 1

        for domain, answer in to_add:
            if dry_run:
                logger.info("[DRY RUN] AdGuardHome: would add rewrite %s → %s", domain, answer)
                added += 1
                continue
            try:
                r = self._session.post(
                    f"{self._url}/control/rewrite/add",
                    json={"domain": domain, "answer": answer},
                )
                r.raise_for_status()
                logger.info("Added rewrite %s → %s", domain, answer)
                added += 1
            except requests.RequestException:
                logger.error("Failed to add rewrite %s → %s", domain, answer, exc_info=True)
                errors += 1

        for domain, answer in unchanged_set:
            logger.debug("Unchanged rewrite %s → %s", domain, answer)

        logger.info(
            "Rewrites: added %d, removed %d, unchanged %d, errors %d",
            added, removed, len(unchanged_set), errors,
        )

    def _sync_clients(
        self, desired: list[Client], dry_run: bool, allow_removals: bool
    ) -> None:
        resp = self._session.get(f"{self._url}/control/clients")
        resp.raise_for_status()
        current_by_name = {c["name"]: c for c in resp.json()["clients"]}
        desired_by_name = {c.name: c for c in desired}

        to_add = set(desired_by_name) - set(current_by_name)
        to_remove = set(current_by_name) - set(desired_by_name)
        common = set(desired_by_name) & set(current_by_name)
        to_update = {
            name for name in common
            if sorted(desired_by_name[name].ids) != sorted(current_by_name[name].get("ids", []))
        }

        added = removed = updated = errors = 0
        for name in to_remove:
            if not allow_removals:
                logger.info("Skipping client removal (input degraded): %s", name)
                continue
            if dry_run:
                logger.info("[DRY RUN] AdGuardHome: would remove client %s", name)
                removed += 1
                continue
            try:
                r = self._session.post(
                    f"{self._url}/control/clients/delete",
                    json={"name": name},
                )
                r.raise_for_status()
                logger.info("Removed client %s", name)
                removed += 1
            except requests.RequestException:
                logger.error("Failed to remove client %s", name, exc_info=True)
                errors += 1

        for name in to_add:
            client = desired_by_name[name]
            payload = {"name": client.name, "ids": client.ids, **_CLIENT_DEFAULTS}
            if dry_run:
                logger.info("[DRY RUN] AdGuardHome: would add client %s (%s)", name, client.ids)
                added += 1
                continue
            try:
                r = self._session.post(
                    f"{self._url}/control/clients/add",
                    json=payload,
                )
                r.raise_for_status()
                logger.info("Added client %s (%s)", name, client.ids)
                added += 1
            except requests.RequestException:
                logger.error("Failed to add client %s", name, exc_info=True)
                errors += 1

        for name in to_update:
            client = desired_by_name[name]
            payload = {
                "name": name,
                "data": {"name": client.name, "ids": client.ids, **_CLIENT_DEFAULTS},
            }
            if dry_run:
                logger.info("[DRY RUN] AdGuardHome: would update client %s", name)
                updated += 1
                continue
            try:
                r = self._session.post(
                    f"{self._url}/control/clients/update",
                    json=payload,
                )
                r.raise_for_status()
                logger.info("Updated client %s", name)
                updated += 1
            except requests.RequestException:
                logger.error("Failed to update client %s", name, exc_info=True)
                errors += 1

        for name in common - to_update:
            logger.debug("Unchanged client %s (%s)", name, current_by_name[name].get("ids", []))

        logger.info(
            "Clients: added %d, removed %d, updated %d, unchanged %d, errors %d",
            added, removed, updated, len(common - to_update), errors,
        )
