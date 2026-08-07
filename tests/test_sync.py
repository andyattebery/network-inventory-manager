from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from network_inventory_manager import sync
from network_inventory_manager._types import (
    DiscoveredClient,
    DsmService,
    HostEntry,
    InventoryError,
    NetworkHostsInventory,
    Settings,
    SourceCache,
)
from network_inventory_manager.sync import build_desired_state, run_sync


def _settings(**overrides) -> Settings:
    base = dict(
        dsm_url="https://dsm.test",
        adguardhome_url="http://adguard.test",
        adguardhome_username="admin",
        adguardhome_password="pass",
        unifi_url="https://unifi.test",
        unifi_username="admin",
        unifi_password="pass",
        local_config_path="/does/not/matter",
    )
    base.update(overrides)
    return Settings(**base)


def _inventory(**overrides) -> NetworkHostsInventory:
    defaults = {
        "homelab_domain": "example.com",
        "homelab_hosts": {"server-01": HostEntry(ip="10.0.0.1")},
        "other_hosts": [],
        "services": {},
    }
    defaults.update(overrides)
    return NetworkHostsInventory(**defaults)


@pytest.fixture
def wired(monkeypatch):
    """Replace every I/O boundary of run_sync with a mock."""
    agh = MagicMock()
    unifi_out = MagicMock()
    load = MagicMock(return_value=_inventory())
    fetch_services = MagicMock(return_value=[])
    discovery = MagicMock(return_value=[])

    monkeypatch.setattr(sync.network_hosts_inventory, "load", load)
    monkeypatch.setattr(sync.dsm, "fetch_services", fetch_services)
    monkeypatch.setattr(sync.unifi_discovery, "fetch", discovery)
    monkeypatch.setattr(sync, "UnifiAPIClient", MagicMock())
    monkeypatch.setattr(sync, "AdGuardHomeOutput", MagicMock(return_value=agh))
    monkeypatch.setattr(sync, "UnifiOutput", MagicMock(return_value=unifi_out))

    return SimpleNamespace(
        agh=agh,
        agh_cls=sync.AdGuardHomeOutput,
        unifi_out=unifi_out,
        unifi_cls=sync.UnifiOutput,
        load=load,
        fetch_services=fetch_services,
        discovery=discovery,
    )


class TestInventoryFailure:
    def test_inventory_failure_skips_cycle(self, wired):
        wired.load.side_effect = InventoryError("op inject failed")
        assert run_sync(_settings()) is False
        # The whole point: no output is even constructed, so nothing can be
        # written or deleted against a missing desired state.
        wired.agh_cls.assert_not_called()
        wired.unifi_cls.assert_not_called()

    def test_inventory_failure_does_not_build_desired_state(self, wired, monkeypatch):
        build = MagicMock()
        monkeypatch.setattr(sync, "build_desired_state", build)
        wired.load.side_effect = InventoryError("boom")
        run_sync(_settings())
        build.assert_not_called()

    def test_inventory_failure_does_not_contact_dsm_or_unifi(self, wired):
        wired.load.side_effect = InventoryError("boom")
        run_sync(_settings())
        wired.fetch_services.assert_not_called()
        wired.discovery.assert_not_called()


class TestRemovalScoping:
    def test_all_inputs_ok_allows_removals(self, wired):
        assert run_sync(_settings()) is True
        assert wired.agh.sync.call_args.args[2] is True

    def test_dsm_failure_with_cache_still_allows_removals(self, wired):
        cache = SourceCache()
        wired.fetch_services.return_value = [
            DsmService(name="Plex", url="https://plex.example.com", hostname="server-01")
        ]
        run_sync(_settings(), cache=cache)  # populates the cache
        assert cache.dsm_rewrites == {("plex.example.com", "10.0.0.1")}

        wired.fetch_services.side_effect = requests.RequestException("down")
        assert run_sync(_settings(), cache=cache) is True
        # Removals stay on, and DSM's last known-good entries are protected
        # instead of freezing every other source's removals.
        assert wired.agh.sync.call_args.args[2] is True
        assert wired.agh.sync.call_args.kwargs["policy"].protected_rewrites == {
            ("plex.example.com", "10.0.0.1")
        }

    def test_dsm_failure_without_cache_blocks_removals(self, wired):
        wired.fetch_services.side_effect = requests.RequestException("down")
        assert run_sync(_settings(), cache=SourceCache()) is True
        assert wired.agh.sync.call_args.args[2] is False
        assert wired.agh.sync.call_args.kwargs["policy"].protected_rewrites == set()

    def test_discovery_failure_with_cache_still_allows_removals(self, wired):
        cache = SourceCache()
        wired.discovery.return_value = [
            DiscoveredClient(name="iPhone", ip="10.0.0.50", mac="aa:bb:cc:dd:ee:ff")
        ]
        run_sync(_settings(), cache=cache)
        assert cache.discovered_clients == {"iPhone"}

        wired.discovery.side_effect = requests.RequestException("down")
        assert run_sync(_settings(), cache=cache) is True
        assert wired.agh.sync.call_args.args[2] is True
        assert wired.agh.sync.call_args.kwargs["policy"].protected_clients == {"iPhone"}

    def test_discovery_failure_without_cache_blocks_removals(self, wired):
        wired.discovery.side_effect = requests.RequestException("down")
        assert run_sync(_settings(), cache=SourceCache()) is True
        assert wired.agh.sync.call_args.args[2] is False

    def test_healthy_dsm_returning_empty_clears_cache(self, wired):
        # DSM is up but returns nothing, so its entries become removable rather
        # than protected — the grace window is then what delays their deletion.
        cache = SourceCache()
        wired.fetch_services.return_value = [
            DsmService(name="Plex", url="https://plex.example.com", hostname="server-01")
        ]
        run_sync(_settings(), cache=cache)
        wired.fetch_services.return_value = []
        run_sync(_settings(), cache=cache)
        assert cache.dsm_rewrites == set()
        assert wired.agh.sync.call_args.kwargs["policy"].protected_rewrites == set()


class TestRemovalPolicy:
    def test_grace_cycles_come_from_settings(self, wired):
        run_sync(_settings(removal_grace_cycles=4))
        assert wired.agh.sync.call_args.kwargs["policy"].grace_cycles == 4

    def test_absence_counters_come_from_the_cache(self, wired):
        # The same dicts must be handed over every cycle, or counts reset each
        # time and the grace window never elapses.
        cache = SourceCache()
        run_sync(_settings(), cache=cache)
        policy = wired.agh.sync.call_args.kwargs["policy"]
        assert policy.rewrite_absences is cache.rewrite_absences
        assert policy.client_absences is cache.client_absences

    def test_ownership_tracking_is_enabled(self, wired):
        # RemovalPolicy defaults it off so a bare policy does nothing special;
        # production must turn it on, or hand-made rewrites get deleted.
        run_sync(_settings())
        assert wired.agh.sync.call_args.kwargs["policy"].track_ownership is True

    def test_adguard_error_marks_cycle_failed(self, wired):
        wired.agh.sync.side_effect = requests.RequestException("boom")
        assert run_sync(_settings()) is False

    def test_unifi_error_marks_cycle_failed(self, wired):
        wired.unifi_out.sync.side_effect = requests.RequestException("boom")
        assert run_sync(_settings()) is False


class TestCase:
    def test_rewrite_domains_are_lowercased(self):
        # AdGuardHome stores domains lowercased; a mixed-case desired set makes
        # every tuple mismatch current_set, which is a full delete-and-recreate.
        inv = _inventory(
            homelab_domain="Example.COM",
            homelab_hosts={"Server-01": HostEntry(ip="10.0.0.1")},
        )
        state = build_desired_state(inv, [], [])
        assert [r.domain for r in state.rewrites] == ["server-01.example.com"]

    def test_service_domains_are_lowercased(self):
        inv = _inventory(
            homelab_domain="Example.COM",
            homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")},
            services={"Plex": SimpleNamespace(hostname="server-01")},
        )
        state = build_desired_state(inv, [], [])
        assert "plex.example.com" in {r.domain for r in state.rewrites}

    def test_other_host_names_are_lowercased(self):
        from network_inventory_manager._types import OtherHost

        inv = _inventory(other_hosts=[OtherHost(hostname="Remote.TS.NET", ip="100.1.2.3")])
        state = build_desired_state(inv, [], [])
        assert "remote.ts.net" in {r.domain for r in state.rewrites}

    def test_client_names_keep_their_case(self):
        # Client names are display names, not DNS.
        inv = _inventory(homelab_hosts={"Server-01": HostEntry(ip="10.0.0.1")})
        state = build_desired_state(inv, [], [])
        assert [c.name for c in state.clients] == ["Server-01"]


class TestProvenance:
    def test_dsm_rewrites_recorded(self):
        inv = _inventory(homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")})
        services = [
            DsmService(name="Plex", url="https://plex.example.com", hostname="server-01")
        ]
        state = build_desired_state(inv, services, [])
        assert state.dsm_rewrites == {("plex.example.com", "10.0.0.1")}

    def test_inventory_rewrites_not_attributed_to_dsm(self):
        # A service already in the inventory wins, and must not be recorded as
        # DSM-owned — otherwise a DSM outage would protect it from ever being
        # removed via the inventory.
        inv = _inventory(
            homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")},
            services={"plex": SimpleNamespace(hostname="server-01")},
        )
        services = [
            DsmService(name="Plex", url="https://plex.example.com", hostname="server-01")
        ]
        state = build_desired_state(inv, services, [])
        assert state.dsm_rewrites == set()

    def test_discovered_clients_recorded(self):
        inv = _inventory(homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")})
        discovered = [DiscoveredClient(name="iPhone", ip="10.0.0.50", mac="aa:bb:cc:dd:ee:ff")]
        state = build_desired_state(inv, [], discovered)
        assert state.discovered_clients == {"iPhone"}

    def test_inventory_client_not_attributed_to_discovery(self):
        inv = _inventory(homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")})
        discovered = [DiscoveredClient(name="Other", ip="10.0.0.1", mac="aa:bb:cc:dd:ee:ff")]
        state = build_desired_state(inv, [], discovered)
        assert state.discovered_clients == set()
