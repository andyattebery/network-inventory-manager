from network_inventory_manager._types import (
    DiscoveredClient,
    DsmService,
    HostEntry,
    NetworkHostsInventory,
    OtherHost,
    ServiceEntry,
)
from network_inventory_manager.sync import build_desired_state


def _inventory(**overrides) -> NetworkHostsInventory:
    defaults = {
        "homelab_domain": "example.com",
        "homelab_hosts": {},
        "other_hosts": [],
        "services": {},
    }
    defaults.update(overrides)
    return NetworkHostsInventory(**defaults)


class TestRewrites:
    def test_hosts_create_rewrites(self):
        inv = _inventory(homelab_hosts={
            "server-01": HostEntry(ip="10.0.0.1"),
            "server-02": HostEntry(ip="10.0.0.2"),
        })
        state = build_desired_state(inv, [], [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["server-01.example.com"] == "10.0.0.1"
        assert rewrites["server-02.example.com"] == "10.0.0.2"

    def test_other_hosts_create_rewrites(self):
        inv = _inventory(other_hosts=[
            OtherHost(hostname="remote.tailnet.ts.net", ip="100.1.2.3"),
        ])
        state = build_desired_state(inv, [], [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["remote.tailnet.ts.net"] == "100.1.2.3"

    def test_inventory_services_create_rewrites(self):
        inv = _inventory(
            homelab_hosts={"docker-01": HostEntry(ip="10.0.0.10")},
            services={"grafana": ServiceEntry(hostname="docker-01")},
        )
        state = build_desired_state(inv, [], [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["grafana.example.com"] == "10.0.0.10"

    def test_dsm_services_create_rewrites(self):
        inv = _inventory(homelab_hosts={"docker-01": HostEntry(ip="10.0.0.10")})
        dsm = [DsmService(name="Sonarr", url="https://sonarr.example.com", hostname="docker-01")]
        state = build_desired_state(inv, dsm, [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["sonarr.example.com"] == "10.0.0.10"

    def test_dsm_skips_external_urls(self):
        inv = _inventory(homelab_hosts={"nas-01": HostEntry(ip="10.0.0.20")})
        dsm = [DsmService(name="Plex", url="https://app.plex.tv", hostname="nas-01")]
        state = build_desired_state(inv, dsm, [])
        rewrites = {r.domain for r in state.rewrites}
        assert "app.plex.tv" not in rewrites

    def test_dsm_skips_unknown_hostname(self):
        inv = _inventory(homelab_hosts={"docker-01": HostEntry(ip="10.0.0.10")})
        dsm = [DsmService(name="Svc", url="https://svc.example.com", hostname="nonexistent")]
        state = build_desired_state(inv, dsm, [])
        rewrites = {r.domain for r in state.rewrites}
        assert "svc.example.com" not in rewrites

    def test_host_wins_over_dsm(self):
        inv = _inventory(homelab_hosts={
            "app": HostEntry(ip="10.0.0.1"),
            "docker-01": HostEntry(ip="10.0.0.10"),
        })
        dsm = [DsmService(name="App", url="https://app.example.com", hostname="docker-01")]
        state = build_desired_state(inv, dsm, [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["app.example.com"] == "10.0.0.1"

    def test_inventory_service_wins_over_dsm(self):
        inv = _inventory(
            homelab_hosts={
                "docker-01": HostEntry(ip="10.0.0.10"),
                "nas-01": HostEntry(ip="10.0.0.20"),
            },
            services={"grafana": ServiceEntry(hostname="nas-01")},
        )
        dsm = [DsmService(name="Grafana", url="https://grafana.example.com", hostname="docker-01")]
        state = build_desired_state(inv, dsm, [])
        rewrites = {r.domain: r.answer for r in state.rewrites}
        assert rewrites["grafana.example.com"] == "10.0.0.20"


class TestClients:
    def test_first_name_wins_shared_ip(self):
        inv = _inventory(homelab_hosts={
            "primary": HostEntry(ip="10.0.0.1"),
            "alias": HostEntry(ip="10.0.0.1"),
        })
        state = build_desired_state(inv, [], [])
        clients_by_ip = {c.ids[0]: c.name for c in state.clients}
        assert clients_by_ip["10.0.0.1"] == "primary"

    def test_discovered_clients_fill_gaps(self):
        inv = _inventory(homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")})
        discovered = [DiscoveredClient(name="iPhone", ip="10.0.0.50", mac="aa:bb:cc:dd:ee:ff")]
        state = build_desired_state(inv, [], discovered)
        clients_by_ip = {c.ids[0]: c.name for c in state.clients}
        assert clients_by_ip["10.0.0.50"] == "iPhone"

    def test_config_wins_over_discovered(self):
        inv = _inventory(homelab_hosts={"server-01": HostEntry(ip="10.0.0.1")})
        discovered = [DiscoveredClient(name="Other Name", ip="10.0.0.1", mac="aa:bb:cc:dd:ee:ff")]
        state = build_desired_state(inv, [], discovered)
        clients_by_ip = {c.ids[0]: c.name for c in state.clients}
        assert clients_by_ip["10.0.0.1"] == "server-01"

    def test_other_hosts_create_clients(self):
        inv = _inventory(other_hosts=[OtherHost(hostname="remote.ts.net", ip="100.1.2.3")])
        state = build_desired_state(inv, [], [])
        clients_by_ip = {c.ids[0]: c.name for c in state.clients}
        assert clients_by_ip["100.1.2.3"] == "remote.ts.net"


class TestDHCPReservations:
    def test_hosts_with_mac(self):
        inv = _inventory(homelab_hosts={
            "server-01": HostEntry(ip="10.0.0.1", mac="AA:BB:CC:DD:EE:FF"),
            "server-02": HostEntry(ip="10.0.0.2"),
        })
        state = build_desired_state(inv, [], [])
        assert len(state.dhcp_reservations) == 1
        res = state.dhcp_reservations[0]
        assert res.name == "server-01"
        assert res.mac == "aa:bb:cc:dd:ee:ff"
        assert res.ip == "10.0.0.1"

    def test_skip_dhcp(self):
        inv = _inventory(homelab_hosts={
            "server-01": HostEntry(ip="10.0.0.1", mac="AA:BB:CC:DD:EE:FF", skip_dhcp=True),
        })
        state = build_desired_state(inv, [], [])
        assert len(state.dhcp_reservations) == 0

    def test_skip_dhcp_keeps_rewrite_and_client(self):
        inv = _inventory(homelab_hosts={
            "server-01": HostEntry(ip="10.0.0.1", mac="AA:BB:CC:DD:EE:FF", skip_dhcp=True),
        })
        state = build_desired_state(inv, [], [])
        assert any(r.domain == "server-01.example.com" for r in state.rewrites)
        assert any(c.name == "server-01" for c in state.clients)
        assert len(state.dhcp_reservations) == 0
