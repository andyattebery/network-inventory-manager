from __future__ import annotations

import json
import logging

import requests

from network_inventory_manager._types import (
    Client,
    DesiredState,
    RemovalPolicy,
    Rewrite,
    SyncStatus,
    TimeoutSession,
)

logger = logging.getLogger(__name__)

_CLIENT_DEFAULTS = {
    "use_global_settings": True,
    "use_global_blocked_services": True,
    "filtering_enabled": False,
    "parental_enabled": False,
    "safebrowsing_enabled": False,
    "safe_search": {"enabled": False},
}

# Thresholds for *reporting* an unusually large removal. Deliberately not a
# guard: an earlier version refused such a cycle outright, which meant a
# legitimately large cleanup wedged the service with no way for it to recover.
# Logging and proceeding keeps the signal without the deadlock.
_LARGE_REMOVAL_FRACTION = 0.25
_LARGE_REMOVAL_MIN = 5

# Ownership record. AdGuardHome's rewrite objects are domain/answer/enabled with
# nowhere to hang metadata, so the set of rewrites NIM created is kept as a
# comment line in the custom filtering rules — the one place AdGuardHome stores
# arbitrary text verbatim. Living there rather than on NIM's disk means ownership
# survives restarts and reaches the replicas via adguardhome-sync, and NIM stays
# stateless. '!' is a filter-syntax comment, so the engine ignores the line.
_OWNED_PREFIX = "! nim-owned "
_OWNED_VERSION = 1


def _select_removable(
    candidates: set,
    absences: dict,
    grace_cycles: int,
    advance: bool,
) -> tuple[set, int]:
    """Split absent entries into those past the grace window and those still in it.

    `absences` counts *consecutive* cycles absent and is mutated in place when
    `advance` is set. Anything no longer a candidate — reappeared in the desired
    state, or now protected — has its count dropped, so the window always measures
    an unbroken run of absences rather than a lifetime total.
    """
    for entry in list(absences):
        if entry not in candidates:
            del absences[entry]

    if grace_cycles <= 0:
        return set(candidates), 0

    removable = set()
    deferred = 0
    for entry in candidates:
        seen = absences.get(entry, 0) + 1
        if seen >= grace_cycles:
            removable.add(entry)
            if advance:
                absences.pop(entry, None)
        else:
            deferred += 1
            if advance:
                absences[entry] = seen
    return removable, deferred


class AdGuardHomeOutput:
    def __init__(self, url: str, username: str, password: str) -> None:
        self._url = url
        self._session = TimeoutSession()
        self._status: SyncStatus | None = None
        self._session.auth = (username, password)

    def sync(
        self,
        desired: DesiredState,
        dry_run: bool,
        allow_removals: bool,
        policy: RemovalPolicy | None = None,
        status: SyncStatus | None = None,
    ) -> None:
        policy = policy or RemovalPolicy()
        self._status = status
        removed = self._sync_rewrites(desired.rewrites, dry_run, allow_removals, policy)
        self._sync_clients(desired.clients, dry_run, allow_removals, policy)
        if policy.track_ownership:
            self._write_owned(desired.rewrites, removed, dry_run)

    def _read_user_rules(self) -> list[str]:
        resp = self._session.get(f"{self._url}/control/filtering/status")
        resp.raise_for_status()
        return list(resp.json().get("user_rules") or [])

    def _read_owned(self) -> set[str] | None:
        """Domains NIM has previously recorded creating, or None if never recorded.

        None and the empty set mean different things: None is "no record yet, fall
        back to inference", empty is "recorded, and NIM owns nothing".
        """
        for rule in self._read_user_rules():
            if not rule.startswith(_OWNED_PREFIX):
                continue
            try:
                payload = json.loads(rule[len(_OWNED_PREFIX):])
                return set(payload["domains"])
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    "Ignoring unparseable ownership record in AdGuardHome user rules; "
                    "it will be rewritten this cycle"
                )
                return None
        return None

    def _write_owned(
        self, desired: list[Rewrite], removed: set[tuple[str, str]], dry_run: bool
    ) -> None:
        """Record what NIM owns, preserving every other custom filtering rule.

        Ownership accumulates rather than tracking the desired set: an entry that
        leaves `desired` must stay owned, or it could never be removed later.
        """
        current_rules = self._read_user_rules()
        previous = self._read_owned() or set()

        owned = (previous | {r.domain for r in desired}) - {d for d, _ in removed}

        line = _OWNED_PREFIX + json.dumps(
            # Sorted so an unchanged set serialises identically every cycle and
            # AdGuardHome is not rewritten (and re-replicated) for nothing.
            {"v": _OWNED_VERSION, "domains": sorted(owned)},
            separators=(",", ":"),
        )
        # Pinned to the top: user_rules is where blocklist overrides accumulate,
        # and those get appended at the bottom.
        new_rules = [line] + [r for r in current_rules if not r.startswith(_OWNED_PREFIX)]

        if new_rules == current_rules:
            logger.debug("Ownership record unchanged (%d domains)", len(owned))
            return
        if dry_run:
            logger.info("[DRY RUN] AdGuardHome: would record %d owned domains", len(owned))
            return

        resp = self._session.post(
            f"{self._url}/control/filtering/set_rules", json={"rules": new_rules}
        )
        resp.raise_for_status()
        logger.info("Recorded %d owned domains in AdGuardHome user rules", len(owned))

    def _report_large_removal(self, kind: str, to_remove: int, current: int) -> None:
        if to_remove < _LARGE_REMOVAL_MIN or current == 0:
            return
        fraction = to_remove / current
        if fraction <= _LARGE_REMOVAL_FRACTION:
            return
        logger.error(
            "Removing %d of %d existing %s (%.0f%%) — unusually large. Proceeding, "
            "but check the inventory and DSM if this was not intended.",
            to_remove, current, kind, 100 * fraction,
        )

    def _sync_rewrites(
        self,
        desired: list[Rewrite],
        dry_run: bool,
        allow_removals: bool,
        policy: RemovalPolicy,
    ) -> set[tuple[str, str]]:
        resp = self._session.get(f"{self._url}/control/rewrite/list")
        resp.raise_for_status()
        current_set = {(r["domain"], r["answer"]) for r in resp.json()}
        desired_set = {(r.domain, r.answer) for r in desired}

        to_add = desired_set - current_set
        # `protected` holds an unreachable input's last known-good entries: not
        # currently desired only because that input could not be consulted.
        candidates = current_set - desired_set - policy.protected_rewrites
        unchanged_set = current_set & desired_set

        if policy.track_ownership:
            owned = self._read_owned()
            if owned is None:
                # No record yet. Claim only what NIM currently wants and already
                # exists — this can under-claim (an entry NIM made before the
                # inventory changed looks hand-made) but never over-claim, so the
                # worst case is a stale record surviving, not a manual one deleted.
                owned = {d for d, _ in current_set & desired_set}
                logger.info(
                    "No ownership record in AdGuardHome; claiming %d existing "
                    "rewrite(s) that match the desired state",
                    len(owned),
                )
            unowned = {e for e in candidates if e[0] not in owned}
            if unowned:
                logger.info(
                    "Leaving %d rewrite(s) NIM does not own: %s",
                    len(unowned), sorted(d for d, _ in unowned)[:5],
                )
            candidates = candidates - unowned

        # Only advance the counters when the result would actually be acted on.
        # A dry run must leave state untouched, and while removals are blocked an
        # entry's absence tells us nothing about whether it should go.
        to_remove, deferred = _select_removable(
            candidates,
            policy.rewrite_absences,
            policy.grace_cycles,
            advance=allow_removals and not dry_run,
        )
        if deferred:
            logger.info(
                "Deferring removal of %d rewrite(s) still inside the %d-cycle grace "
                "window: %s",
                deferred, policy.grace_cycles,
                sorted(d for d, _ in candidates - to_remove)[:5],
            )
        if allow_removals:
            self._report_large_removal("rewrites", len(to_remove), len(current_set))

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
            "Rewrites: added %d, removed %d, deferred %d, unchanged %d, errors %d",
            added, removed, deferred, len(unchanged_set), errors,
        )
        if self._status is not None:
            self._status.rewrites_added += added
            self._status.rewrites_removed += removed
            self._status.removals_deferred = deferred
        # Only entries actually deleted drop out of the ownership record; a
        # blocked or dry-run removal must stay owned.
        return to_remove if (allow_removals and not dry_run) else set()

    def _sync_clients(
        self,
        desired: list[Client],
        dry_run: bool,
        allow_removals: bool,
        policy: RemovalPolicy,
    ) -> None:
        resp = self._session.get(f"{self._url}/control/clients")
        resp.raise_for_status()
        current_by_name = {c["name"]: c for c in (resp.json()["clients"] or [])}
        desired_by_name = {c.name: c for c in desired}

        to_add = set(desired_by_name) - set(current_by_name)
        candidates = set(current_by_name) - set(desired_by_name) - policy.protected_clients
        common = set(desired_by_name) & set(current_by_name)
        to_update = {
            name for name in common
            if sorted(desired_by_name[name].ids) != sorted(current_by_name[name].get("ids", []))
        }

        to_remove, deferred = _select_removable(
            candidates,
            policy.client_absences,
            policy.grace_cycles,
            advance=allow_removals and not dry_run,
        )
        if deferred:
            logger.info(
                "Deferring removal of %d client(s) still inside the %d-cycle grace "
                "window: %s",
                deferred, policy.grace_cycles, sorted(candidates - to_remove)[:5],
            )
        if allow_removals:
            self._report_large_removal("clients", len(to_remove), len(current_by_name))

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
            "Clients: added %d, removed %d, deferred %d, updated %d, unchanged %d, errors %d",
            added, removed, deferred, updated, len(common - to_update), errors,
        )
