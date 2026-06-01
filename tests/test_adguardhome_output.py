import responses

from network_inventory_manager._types import Client, DesiredState, Rewrite
from network_inventory_manager.outputs.adguardhome import AdGuardHomeOutput

URL = "http://adguard.test"


def _desired(
    rewrites: list[Rewrite] | None = None,
    clients: list[Client] | None = None,
) -> DesiredState:
    return DesiredState(
        rewrites=rewrites or [],
        clients=clients or [],
        dhcp_reservations=[],
    )


def _output() -> AdGuardHomeOutput:
    return AdGuardHomeOutput(URL, "admin", "pass")


class TestSyncRewrites:
    @responses.activate
    def test_adds_new_removes_stale(self):
        responses.get(f"{URL}/control/rewrite/list", json=[
            {"domain": "keep.example.com", "answer": "10.0.0.1"},
            {"domain": "stale.example.com", "answer": "10.0.0.99"},
        ])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})
        responses.post(f"{URL}/control/rewrite/delete", json={})
        responses.post(f"{URL}/control/rewrite/add", json={})

        desired = _desired(rewrites=[
            Rewrite(domain="keep.example.com", answer="10.0.0.1"),
            Rewrite(domain="new.example.com", answer="10.0.0.2"),
        ])
        _output().sync(desired, dry_run=False, allow_removals=True)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 2
        assert any("delete" in c.request.url for c in posts)
        assert any("add" in c.request.url for c in posts)

    @responses.activate
    def test_allow_removals_false_skips_delete(self):
        responses.get(f"{URL}/control/rewrite/list", json=[
            {"domain": "stale.example.com", "answer": "10.0.0.99"},
        ])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})

        _output().sync(_desired(), dry_run=False, allow_removals=False)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 0

    @responses.activate
    def test_dry_run_no_posts(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})

        desired = _desired(rewrites=[Rewrite(domain="new.example.com", answer="10.0.0.1")])
        _output().sync(desired, dry_run=True, allow_removals=True)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 0

    @responses.activate
    def test_individual_write_failure_continues(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.post(f"{URL}/control/rewrite/add", status=500)
        responses.post(f"{URL}/control/rewrite/add", json={})
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})

        desired = _desired(rewrites=[
            Rewrite(domain="a.example.com", answer="10.0.0.1"),
            Rewrite(domain="b.example.com", answer="10.0.0.2"),
        ])
        _output().sync(desired, dry_run=False, allow_removals=True)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 2


class TestSyncClients:
    @responses.activate
    def test_adds_removes_updates(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={
            "clients": [
                {"name": "keep", "ids": ["10.0.0.1"]},
                {"name": "stale", "ids": ["10.0.0.99"]},
                {"name": "changed", "ids": ["10.0.0.50"]},
            ],
            "auto_clients": [],
        })
        responses.post(f"{URL}/control/clients/delete", json={})
        responses.post(f"{URL}/control/clients/add", json={})
        responses.post(f"{URL}/control/clients/update", json={})

        desired = _desired(clients=[
            Client(name="keep", ids=["10.0.0.1"]),
            Client(name="new", ids=["10.0.0.2"]),
            Client(name="changed", ids=["10.0.0.55"]),
        ])
        _output().sync(desired, dry_run=False, allow_removals=True)

        post_urls = [c.request.url for c in responses.calls if c.request.method == "POST"]
        assert any("delete" in u for u in post_urls)
        assert any("/clients/add" in u for u in post_urls)
        assert any("update" in u for u in post_urls)

    @responses.activate
    def test_null_clients_response(self):
        """Fresh AGH instance returns null instead of empty list."""
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={"clients": None, "auto_clients": []})
        responses.post(f"{URL}/control/clients/add", json={})

        desired = _desired(clients=[Client(name="new", ids=["10.0.0.1"])])
        _output().sync(desired, dry_run=False, allow_removals=True)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 1
        assert "/clients/add" in posts[0].request.url

    @responses.activate
    def test_client_removals_blocked(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={
            "clients": [{"name": "stale", "ids": ["10.0.0.99"]}],
            "auto_clients": [],
        })

        _output().sync(_desired(), dry_run=False, allow_removals=False)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 0
