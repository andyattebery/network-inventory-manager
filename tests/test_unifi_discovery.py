from unittest.mock import MagicMock

from network_inventory_manager.inputs.unifi_discovery import fetch


def _mock_client(active: list[dict], configured: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_active_clients.return_value = active
    client.get_configured_users.return_value = configured
    return client


class TestFetch:
    def test_merge_active_ip_preferred(self):
        client = _mock_client(
            active=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.50", "name": "Phone"}],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.99", "name": "Phone"}],
        )
        results = fetch(client, "default")
        assert len(results) == 1
        assert results[0].ip == "10.0.0.50"

    def test_configured_name_preferred(self):
        client = _mock_client(
            active=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.50", "name": "active-name"}],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "configured-name"}],
        )
        results = fetch(client, "default")
        assert results[0].name == "configured-name"

    def test_falls_back_to_hostname(self):
        client = _mock_client(
            active=[],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "hostname": "my-host"}],
        )
        results = fetch(client, "default")
        assert results[0].name == "my-host"

    def test_filters_without_name(self):
        client = _mock_client(
            active=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"}],
            configured=[],
        )
        results = fetch(client, "default")
        assert len(results) == 0

    def test_filters_without_ip(self):
        client = _mock_client(
            active=[],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone"}],
        )
        results = fetch(client, "default")
        assert len(results) == 0

    def test_dedup_by_mac_case_insensitive(self):
        client = _mock_client(
            active=[{"mac": "AA:BB:CC:DD:EE:FF", "ip": "10.0.0.1"}],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "name": "Device"}],
        )
        results = fetch(client, "default")
        assert len(results) == 1

    def test_active_fills_name_for_configured_without_name(self):
        client = _mock_client(
            active=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "name": "Active Name"}],
            configured=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.2"}],
        )
        results = fetch(client, "default")
        assert results[0].name == "Active Name"
        assert results[0].ip == "10.0.0.1"
