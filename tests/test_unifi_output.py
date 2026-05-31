from unittest.mock import MagicMock

import requests

from network_inventory_manager._types import DHCPReservation, DesiredState
from network_inventory_manager.outputs.unifi import UnifiOutput


def _desired(reservations: list[DHCPReservation]) -> DesiredState:
    return DesiredState(rewrites=[], clients=[], dhcp_reservations=reservations)


def _mock_client(current_users: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_configured_users.return_value = current_users
    return client


class TestSync:
    def test_unchanged(self):
        client = _mock_client([{
            "mac": "aa:bb:cc:dd:ee:ff",
            "_id": "123",
            "use_fixedip": True,
            "fixed_ip": "10.0.0.1",
        }])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        client.update_user.assert_not_called()
        client.create_user.assert_not_called()

    def test_update_ip_differs(self):
        client = _mock_client([{
            "mac": "aa:bb:cc:dd:ee:ff",
            "_id": "123",
            "use_fixedip": True,
            "fixed_ip": "10.0.0.99",
        }])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        client.update_user.assert_called_once_with("default", "123", {
            "name": "server-01",
            "use_fixedip": True,
            "fixed_ip": "10.0.0.1",
        })

    def test_update_fixedip_false(self):
        client = _mock_client([{
            "mac": "aa:bb:cc:dd:ee:ff",
            "_id": "123",
            "use_fixedip": False,
            "fixed_ip": "10.0.0.1",
        }])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        client.update_user.assert_called_once()

    def test_create_new(self):
        client = _mock_client([])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        client.create_user.assert_called_once_with("default", {
            "mac": "aa:bb:cc:dd:ee:ff",
            "name": "server-01",
            "use_fixedip": True,
            "fixed_ip": "10.0.0.1",
        })

    def test_dry_run_no_mutations(self):
        client = _mock_client([])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=True)
        client.create_user.assert_not_called()
        client.update_user.assert_not_called()

    def test_mac_case_insensitive(self):
        client = _mock_client([{
            "mac": "AA:BB:CC:DD:EE:FF",
            "_id": "123",
            "use_fixedip": True,
            "fixed_ip": "10.0.0.1",
        }])
        desired = _desired([DHCPReservation(name="server-01", mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.1")])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        client.update_user.assert_not_called()
        client.create_user.assert_not_called()

    def test_error_isolation(self):
        client = _mock_client([])
        client.create_user.side_effect = [requests.RequestException("fail"), None]
        desired = _desired([
            DHCPReservation(name="a", mac="aa:aa:aa:aa:aa:aa", ip="10.0.0.1"),
            DHCPReservation(name="b", mac="bb:bb:bb:bb:bb:bb", ip="10.0.0.2"),
        ])
        UnifiOutput(client).sync(desired, "default", dry_run=False)
        assert client.create_user.call_count == 2
