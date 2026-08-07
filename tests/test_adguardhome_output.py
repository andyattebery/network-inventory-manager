import json

import responses

from network_inventory_manager._types import (
    Client,
    DesiredState,
    RemovalPolicy,
    Rewrite,
)
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


class TestProtectedEntries:
    @responses.activate
    def test_protected_rewrite_not_removed(self):
        # A DSM outage's last known-good entries: absent from desired only because
        # DSM could not be consulted.
        responses.get(f"{URL}/control/rewrite/list", json=[
            {"domain": "plex.example.com", "answer": "10.0.0.1"},
            {"domain": "stale.example.com", "answer": "10.0.0.99"},
        ])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})
        responses.post(f"{URL}/control/rewrite/delete", json={})

        _output().sync(
            _desired(), dry_run=False, allow_removals=True,
            policy=RemovalPolicy(protected_rewrites={("plex.example.com", "10.0.0.1")}),
        )

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 1
        assert "stale.example.com" in posts[0].request.body.decode()

    @responses.activate
    def test_protected_client_not_removed(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={
            "clients": [
                {"name": "iPhone", "ids": ["10.0.0.50"]},
                {"name": "stale", "ids": ["10.0.0.99"]},
            ],
            "auto_clients": [],
        })
        responses.post(f"{URL}/control/clients/delete", json={})

        _output().sync(
            _desired(), dry_run=False, allow_removals=True,
            policy=RemovalPolicy(protected_clients={"iPhone"}),
        )

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 1
        assert "stale" in posts[0].request.body.decode()


class TestGraceWindow:
    """An entry absent from desired is deleted only after N consecutive cycles.

    This is what stops a service stopped for debugging from losing its DNS, and
    what a volume threshold could never catch: a single entry flapping.
    """

    def _stale_listing(self):
        responses.get(f"{URL}/control/rewrite/list", json=[
            {"domain": "stale.example.com", "answer": "10.0.0.99"},
        ])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})
        responses.post(f"{URL}/control/rewrite/delete", json={})

    @responses.activate
    def test_absent_below_threshold_is_not_removed(self):
        self._stale_listing()
        policy = RemovalPolicy(grace_cycles=3)

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=policy)

        assert [c for c in responses.calls if c.request.method == "POST"] == []
        assert policy.rewrite_absences[("stale.example.com", "10.0.0.99")] == 1

    @responses.activate
    def test_removed_once_threshold_reached(self):
        self._stale_listing()
        # Two prior consecutive absences; this cycle is the third.
        policy = RemovalPolicy(
            grace_cycles=3,
            rewrite_absences={("stale.example.com", "10.0.0.99"): 2},
        )

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=policy)

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 1
        assert "stale.example.com" in posts[0].request.body.decode()
        # Counter cleared, so a re-added entry starts from zero.
        assert ("stale.example.com", "10.0.0.99") not in policy.rewrite_absences

    @responses.activate
    def test_reappearing_resets_the_counter(self):
        responses.get(f"{URL}/control/rewrite/list", json=[
            {"domain": "flappy.example.com", "answer": "10.0.0.5"},
        ])
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})
        policy = RemovalPolicy(
            grace_cycles=3,
            rewrite_absences={("flappy.example.com", "10.0.0.5"): 2},
        )

        # Back in the desired set this cycle.
        desired = _desired(rewrites=[Rewrite(domain="flappy.example.com", answer="10.0.0.5")])
        _output().sync(desired, dry_run=False, allow_removals=True, policy=policy)

        assert policy.rewrite_absences == {}

    @responses.activate
    def test_fresh_policy_removes_nothing(self):
        # A restart drops the counters, which must delay removals rather than
        # perform them.
        self._stale_listing()

        _output().sync(
            _desired(), dry_run=False, allow_removals=True,
            policy=RemovalPolicy(grace_cycles=8),
        )

        assert [c for c in responses.calls if c.request.method == "POST"] == []

    @responses.activate
    def test_dry_run_does_not_advance_counters(self):
        self._stale_listing()
        policy = RemovalPolicy(grace_cycles=3)

        _output().sync(_desired(), dry_run=True, allow_removals=True, policy=policy)

        assert policy.rewrite_absences == {}

    @responses.activate
    def test_blocked_removals_do_not_advance_counters(self):
        # While an input is degraded, absence says nothing about whether the entry
        # should go, so the window must not tick down.
        self._stale_listing()
        policy = RemovalPolicy(grace_cycles=3)

        _output().sync(_desired(), dry_run=False, allow_removals=False, policy=policy)

        assert policy.rewrite_absences == {}

    @responses.activate
    def test_zero_grace_removes_immediately(self):
        self._stale_listing()

        _output().sync(
            _desired(), dry_run=False, allow_removals=True,
            policy=RemovalPolicy(grace_cycles=0),
        )

        posts = [c for c in responses.calls if c.request.method == "POST"]
        assert len(posts) == 1

    @responses.activate
    def test_clients_use_the_same_window(self):
        responses.get(f"{URL}/control/rewrite/list", json=[])
        responses.get(f"{URL}/control/clients", json={
            "clients": [{"name": "stale", "ids": ["10.0.0.99"]}],
            "auto_clients": [],
        })
        policy = RemovalPolicy(grace_cycles=3)

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=policy)

        assert [c for c in responses.calls if c.request.method == "POST"] == []
        assert policy.client_absences["stale"] == 1

    @responses.activate
    def test_protected_entry_never_accrues_absences(self):
        # Protected entries are not removal candidates at all, so they must not
        # quietly age toward deletion while their source is unreachable.
        self._stale_listing()
        policy = RemovalPolicy(
            grace_cycles=3,
            protected_rewrites={("stale.example.com", "10.0.0.99")},
        )

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=policy)

        assert [c for c in responses.calls if c.request.method == "POST"] == []
        assert policy.rewrite_absences == {}


class TestOwnership:
    """Removals are restricted to rewrites NIM recorded creating.

    The record lives in AdGuardHome's custom filtering rules rather than on NIM's
    disk, so it survives restarts and reaches replicas via adguardhome-sync, and
    NIM stays stateless.
    """

    # A real hand-written rule from the production instance. Preserving lines like
    # this is the hard requirement, not a nicety.
    MANUAL = "@@||checkin.ventrata.com^$important"

    def _wire(self, rewrites, user_rules):
        responses.get(f"{URL}/control/rewrite/list", json=rewrites)
        responses.get(f"{URL}/control/clients", json={"clients": [], "auto_clients": []})
        responses.get(f"{URL}/control/filtering/status", json={"user_rules": user_rules})
        responses.post(f"{URL}/control/filtering/set_rules", json={})
        responses.post(f"{URL}/control/rewrite/delete", json={})

    def _policy(self, **kw):
        return RemovalPolicy(track_ownership=True, **kw)

    def _set_rules_body(self):
        for call in responses.calls:
            if "set_rules" in call.request.url:
                return json.loads(call.request.body)["rules"]
        return None

    @responses.activate
    def test_unowned_entry_is_never_removed(self):
        owned = f'{{"v":1,"domains":["mine.example.com"]}}'
        self._wire(
            [{"domain": "manual.example.com", "answer": "10.0.0.9"}],
            [f"! nim-owned {owned}", self.MANUAL],
        )

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=self._policy())

        assert [c for c in responses.calls if "rewrite/delete" in c.request.url] == []

    @responses.activate
    def test_owned_entry_is_removed(self):
        owned = '{"v":1,"domains":["mine.example.com"]}'
        self._wire(
            [{"domain": "mine.example.com", "answer": "10.0.0.9"}],
            [f"! nim-owned {owned}"],
        )

        _output().sync(_desired(), dry_run=False, allow_removals=True, policy=self._policy())

        deletes = [c for c in responses.calls if "rewrite/delete" in c.request.url]
        assert len(deletes) == 1

    @responses.activate
    def test_manual_rules_survive_in_order(self):
        other = "||ads.example.com^"
        self._wire([], [self.MANUAL, other])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        rules = self._set_rules_body()
        assert rules[1:] == [self.MANUAL, other]

    @responses.activate
    def test_record_is_pinned_to_the_top(self):
        self._wire([], [self.MANUAL])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        rules = self._set_rules_body()
        assert rules[0].startswith("! nim-owned ")
        assert json.loads(rules[0][len("! nim-owned "):])["domains"] == ["a.example.com"]

    @responses.activate
    def test_record_replaced_not_accumulated(self):
        stale = '{"v":1,"domains":["old.example.com"]}'
        self._wire([], [f"! nim-owned {stale}", self.MANUAL])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        rules = self._set_rules_body()
        assert len([r for r in rules if r.startswith("! nim-owned ")]) == 1

    @responses.activate
    def test_ownership_accumulates_beyond_desired(self):
        # An entry that left the desired set must stay owned, or it could never be
        # removed on a later cycle.
        owned = '{"v":1,"domains":["gone.example.com"]}'
        self._wire([], [f"! nim-owned {owned}"])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        rules = self._set_rules_body()
        domains = json.loads(rules[0][len("! nim-owned "):])["domains"]
        assert domains == ["a.example.com", "gone.example.com"]

    @responses.activate
    def test_unchanged_record_skips_the_write(self):
        owned = '{"v":1,"domains":["a.example.com"]}'
        self._wire(
            [{"domain": "a.example.com", "answer": "10.0.0.1"}],
            [f"! nim-owned {owned}"],
        )

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        assert self._set_rules_body() is None

    @responses.activate
    def test_domains_are_sorted(self):
        self._wire([], [])

        desired = _desired(rewrites=[
            Rewrite(domain="z.example.com", answer="10.0.0.2"),
            Rewrite(domain="a.example.com", answer="10.0.0.1"),
        ])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        domains = json.loads(self._set_rules_body()[0][len("! nim-owned "):])["domains"]
        assert domains == ["a.example.com", "z.example.com"]

    @responses.activate
    def test_bootstrap_claims_only_current_and_desired(self):
        self._wire(
            [
                {"domain": "mine.example.com", "answer": "10.0.0.1"},
                {"domain": "manual.example.com", "answer": "10.0.0.9"},
            ],
            [self.MANUAL],
        )

        desired = _desired(rewrites=[Rewrite(domain="mine.example.com", answer="10.0.0.1")])
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        # The manual entry is neither claimed nor deleted.
        assert [c for c in responses.calls if "rewrite/delete" in c.request.url] == []
        domains = json.loads(self._set_rules_body()[0][len("! nim-owned "):])["domains"]
        assert domains == ["mine.example.com"]

    @responses.activate
    def test_corrupt_record_is_rewritten_not_fatal(self):
        self._wire([], ["! nim-owned {not json", self.MANUAL])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        responses.post(f"{URL}/control/rewrite/add", json={})
        _output().sync(desired, dry_run=False, allow_removals=True, policy=self._policy())

        rules = self._set_rules_body()
        assert len([r for r in rules if r.startswith("! nim-owned ")]) == 1
        assert self.MANUAL in rules

    @responses.activate
    def test_dry_run_writes_nothing(self):
        self._wire([], [self.MANUAL])

        desired = _desired(rewrites=[Rewrite(domain="a.example.com", answer="10.0.0.1")])
        _output().sync(desired, dry_run=True, allow_removals=True, policy=self._policy())

        assert self._set_rules_body() is None
