import json
import threading
from http.server import HTTPServer

import pytest
import requests

from network_inventory_manager._types import HTTP_TIMEOUT_SECONDS, SyncStatus, TimeoutSession
from network_inventory_manager.__main__ import SyncHandler


@pytest.fixture
def server():
    """A real HTTPServer on an ephemeral port.

    Worth the setup: /health's whole job is what an external monitor sees, and a
    mocked handler would not exercise the status codes that carry that signal.
    """
    status = SyncStatus()
    SyncHandler.sync_event = threading.Event()
    SyncHandler.status = status
    SyncHandler.stale_after = 3600

    httpd = HTTPServer(("127.0.0.1", 0), SyncHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield url, status
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestHealth:
    def test_503_before_any_cycle(self, server):
        url, _ = server
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 503
        assert "no sync cycle" in resp.json()["reason"]

    def test_503_after_failed_cycle(self, server):
        url, status = server
        status.record(applied=False, error="op inject failed")
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 503
        assert resp.json()["last_error"] == "op inject failed"

    def test_200_after_good_cycle(self, server):
        url, status = server
        status.record(applied=True, error=None)
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["last_cycle_applied"] is True

    def test_503_when_stale_despite_success(self, server):
        # The hang case: the last cycle succeeded but was hours ago, so the loop
        # is wedged. Reporting only the last outcome would say 200 forever.
        url, status = server
        status.record(applied=True, error=None, now=1000.0)
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 503
        assert "over the" in resp.json()["reason"]

    def test_unknown_path_404s(self, server):
        url, _ = server
        assert requests.get(f"{url}/nope", timeout=5).status_code == 404


class TestMetrics:
    def test_exposition_format(self, server):
        url, status = server
        status.record(applied=True, error=None)
        status.rewrites_added = 3
        status.rewrites_removed = 1
        status.removals_deferred = 2
        status.inventory_failures = 4

        resp = requests.get(f"{url}/metrics", timeout=5)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/plain")

        body = resp.text
        metrics = {}
        for line in body.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            name, value = line.split(" ", 1)
            metrics[name] = float(value)

        assert metrics["nim_last_sync_success"] == 1
        assert metrics["nim_healthy"] == 1
        assert metrics["nim_rewrites_added_total"] == 3
        assert metrics["nim_rewrites_removed_total"] == 1
        assert metrics["nim_removals_deferred"] == 2
        assert metrics["nim_inventory_load_failures_total"] == 4
        # Every series must be preceded by HELP and TYPE.
        for name in metrics:
            assert f"# HELP {name} " in body
            assert f"# TYPE {name} " in body

    def test_reports_unhealthy_before_first_cycle(self, server):
        url, _ = server
        body = requests.get(f"{url}/metrics", timeout=5).text
        assert "nim_healthy 0" in body


class TestSyncStatus:
    def test_health_needs_no_server(self):
        status = SyncStatus()
        healthy, body = status.health(stale_after=None)
        assert healthy is False
        assert body["last_cycle_at"] is None

    def test_no_staleness_bound_stays_healthy(self):
        # One-shot mode has no interval, so there is nothing to be stale against.
        status = SyncStatus()
        status.record(applied=True, error=None, now=1000.0)
        healthy, _ = status.health(stale_after=None, now=10_000_000.0)
        assert healthy is True

    def test_snapshot_is_a_copy(self):
        status = SyncStatus()
        status.record(applied=True, error=None)
        snap = status.snapshot()
        snap["last_cycle_applied"] = False
        assert status.snapshot()["last_cycle_applied"] is True


class TestTimeoutSession:
    def test_default_timeout_is_applied(self, monkeypatch):
        # requests waits forever by default, and NIM has one sync thread — a
        # single call without a timeout wedges every later cycle.
        seen = {}

        def fake_request(self, method, url, **kwargs):
            seen.update(kwargs)
            return "sentinel"

        monkeypatch.setattr(requests.Session, "request", fake_request)
        assert TimeoutSession().get("http://example.test") == "sentinel"
        assert seen["timeout"] == HTTP_TIMEOUT_SECONDS

    def test_explicit_timeout_wins(self, monkeypatch):
        seen = {}

        def fake_request(self, method, url, **kwargs):
            seen.update(kwargs)
            return "sentinel"

        monkeypatch.setattr(requests.Session, "request", fake_request)
        TimeoutSession().get("http://example.test", timeout=1)
        assert seen["timeout"] == 1
