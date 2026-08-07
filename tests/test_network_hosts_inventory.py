import subprocess
from types import SimpleNamespace

import pytest
import responses

from network_inventory_manager._types import InventoryError
from network_inventory_manager.inputs import network_hosts_inventory as nhi

TEMPLATE = """\
homelab_domain: {{ op://Home Lab/Home Lab/domains/internal }}
homelab_hosts:
  server-01:
    ip: 192.168.1.10
    mac: {{ op://Home Lab/server-01/hardware/mac address }}
"""

RESOLVED = """\
homelab_domain: example.com
homelab_hosts:
  server-01:
    ip: 192.168.1.10
    mac: AA:BB:CC:DD:EE:FF
"""


def _fake_op(monkeypatch, *, stdout="", stderr="", returncode=0, raises=None, calls=None):
    def fake_run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(nhi.subprocess, "run", fake_run)


def _load(tmp_path, template=TEMPLATE):
    p = tmp_path / "inventory.yaml.tpl"
    p.write_text(template)
    return nhi.load(local_config_path=str(p), op_service_account_token="ops_fake")


class TestResolveTemplate:
    def test_resolves_and_parses(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=RESOLVED)
        inv = _load(tmp_path)
        assert inv.homelab_domain == "example.com"
        assert inv.homelab_hosts["server-01"].mac == "AA:BB:CC:DD:EE:FF"

    def test_nonzero_returncode_raises(self, tmp_path, monkeypatch):
        # The incident: the service account cannot see a Personal vault.
        _fake_op(
            monkeypatch,
            returncode=1,
            stderr='[ERROR] "Personal" isn\'t a vault in this account.',
        )
        with pytest.raises(InventoryError, match="op inject failed"):
            _load(tmp_path)

    def test_stderr_is_in_message(self, tmp_path, monkeypatch):
        _fake_op(
            monkeypatch,
            returncode=1,
            stderr='[ERROR] "Personal" isn\'t a vault in this account.',
        )
        with pytest.raises(InventoryError) as excinfo:
            _load(tmp_path)
        assert "Personal" in str(excinfo.value)

    def test_stdout_not_in_message(self, tmp_path, monkeypatch):
        # A partial run can leave resolved secrets in stdout.
        _fake_op(monkeypatch, returncode=1, stdout="mac: SECRET-VALUE", stderr="boom")
        with pytest.raises(InventoryError) as excinfo:
            _load(tmp_path)
        assert "SECRET-VALUE" not in str(excinfo.value)

    def test_missing_op_binary_raises(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, raises=FileNotFoundError())
        with pytest.raises(InventoryError, match="1Password CLI"):
            _load(tmp_path)

    def test_timeout_raises(self, tmp_path, monkeypatch):
        _fake_op(
            monkeypatch,
            raises=subprocess.TimeoutExpired(cmd=["op"], timeout=60),
        )
        with pytest.raises(InventoryError, match="timed out"):
            _load(tmp_path)

    def test_timeout_is_passed_to_subprocess(self, tmp_path, monkeypatch):
        calls = []
        _fake_op(monkeypatch, stdout=RESOLVED, calls=calls)
        _load(tmp_path)
        assert calls[0][1]["timeout"] == nhi._OP_INJECT_TIMEOUT_SECONDS

    def test_no_refs_skips_op(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, raises=AssertionError("op must not run"))
        inv = _load(tmp_path, template=RESOLVED)
        assert inv.homelab_domain == "example.com"

    def test_success_with_unresolved_output_raises(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=TEMPLATE)
        with pytest.raises(InventoryError, match="unresolved"):
            _load(tmp_path)


class TestValidation:
    # Note on layering: a literal `{{ op:// }}` in op's output is caught by the
    # unresolved-output guard in _resolve_template before field validation runs,
    # so these tests use *mangled* values (a ref that lost its braces, an empty
    # field, a malformed name) to exercise the validators themselves. The
    # unresolved-ref path is covered by TestResolveTemplate.

    def test_incident_shape_rejected(self, tmp_path, monkeypatch):
        # Exactly what the old _quote_op_refs fallback produced and fed to
        # AdGuardHome. Whichever layer catches it, it must not load.
        _fake_op(monkeypatch, stdout=(
            'homelab_domain: "{{ op://Personal/Home Lab/domains/internal }}"\n'
            "homelab_hosts:\n  server-01:\n    ip: 192.168.1.10\n"
        ))
        with pytest.raises(InventoryError):
            _load(tmp_path)

    def test_mangled_domain_ref_rejected(self, tmp_path, monkeypatch):
        # Braces lost, so the unresolved-output guard does not fire — this is the
        # case the homelab_domain validator exists for.
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: op://Personal/Home Lab/domains/internal\n"
            "homelab_hosts:\n  server-01:\n    ip: 192.168.1.10\n"
        ))
        with pytest.raises(InventoryError, match="homelab_domain"):
            _load(tmp_path)

    def test_empty_domain_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout="homelab_domain:\nhomelab_hosts: {}\n")
        with pytest.raises(InventoryError, match="homelab_domain"):
            _load(tmp_path)

    def test_malformed_domain_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout="homelab_domain: not a domain!\nhomelab_hosts: {}\n")
        with pytest.raises(InventoryError, match="homelab_domain"):
            _load(tmp_path)

    def test_invalid_host_ip_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01:\n    ip: not-an-ip\n"
        ))
        with pytest.raises(InventoryError, match=r"homelab_hosts\.server-01\.ip"):
            _load(tmp_path)

    def test_numeric_host_ip_rejected(self, tmp_path, monkeypatch):
        # `ip: 10.0` parses as a float, not a string.
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01:\n    ip: 10.0\n"
        ))
        with pytest.raises(InventoryError, match=r"homelab_hosts\.server-01\.ip"):
            _load(tmp_path)

    def test_invalid_host_key_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            'homelab_hosts:\n  "not a hostname!":\n    ip: 10.0.0.1\n'
        ))
        with pytest.raises(InventoryError, match="homelab_hosts key"):
            _load(tmp_path)

    def test_host_entry_not_a_mapping_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01: 10.0.0.1\n"
        ))
        with pytest.raises(InventoryError, match=r"homelab_hosts\.server-01 is not a mapping"):
            _load(tmp_path)

    def test_other_host_hostname_validated(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts: {}\n"
            "other_hosts:\n  - hostname: -bad-.hostname\n    ip: 10.0.0.1\n"
        ))
        with pytest.raises(InventoryError, match=r"other_hosts\[0\]\.hostname"):
            _load(tmp_path)

    def test_other_host_ip_validated(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts: {}\n"
            "other_hosts:\n  - hostname: remote.ts.net\n    ip: nope\n"
        ))
        with pytest.raises(InventoryError, match=r"other_hosts\[0\]\.ip"):
            _load(tmp_path)

    def test_service_hostname_validated(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts: {}\n"
            "services:\n  plex:\n    hostname:\n"
        ))
        with pytest.raises(InventoryError, match=r"services\.plex\.hostname"):
            _load(tmp_path)

    def test_service_key_validated(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts: {}\n"
            'services:\n  "bad key!":\n    hostname: server-01\n'
        ))
        with pytest.raises(InventoryError, match="services key"):
            _load(tmp_path)

    def test_all_errors_reported_together(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: not a domain!\n"
            "homelab_hosts:\n  server-01:\n    ip: not-an-ip\n"
        ))
        with pytest.raises(InventoryError) as excinfo:
            _load(tmp_path)
        message = str(excinfo.value)
        assert "2 problem(s)" in message
        assert "homelab_domain" in message
        assert "homelab_hosts.server-01.ip" in message

    def test_not_a_mapping_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout="- just\n- a\n- list\n")
        with pytest.raises(InventoryError, match="did not parse to a mapping"):
            _load(tmp_path)

    def test_invalid_yaml_rejected(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout="homelab_domain: [unclosed\n")
        with pytest.raises(InventoryError, match="not valid YAML"):
            _load(tmp_path)

    def test_valid_inventory_passes(self, tmp_path, monkeypatch):
        # Dotted keys and an IPv6 other_host, both present in real inventories.
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n"
            "  bmc.nas-host-01:\n    ip: 192.168.1.230\n"
            "  backup-01:\n    ip: 192.168.1.249\n    skip_dhcp: true\n"
            "other_hosts:\n  - hostname: remote.ts.net\n    ip: 'fd7a::1'\n"
            "services:\n  llama-swap.htpc-01:\n    hostname: backup-01\n"
        ))
        inv = _load(tmp_path)
        assert set(inv.homelab_hosts) == {"bmc.nas-host-01", "backup-01"}
        assert inv.homelab_hosts["backup-01"].skip_dhcp is True
        assert inv.other_hosts[0].ip == "fd7a::1"
        assert inv.services["llama-swap.htpc-01"].hostname == "backup-01"


class TestMacHandling:
    def test_invalid_mac_is_not_fatal(self, tmp_path, monkeypatch):
        # UniFi output never deletes, so a dropped MAC costs one reservation and
        # cannot destroy existing state. Deliberately non-fatal.
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01:\n    ip: 10.0.0.1\n    mac: not-a-mac\n"
        ))
        inv = _load(tmp_path)
        assert inv.homelab_hosts["server-01"].mac is None

    def test_mangled_mac_ref_is_not_fatal(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01:\n    ip: 10.0.0.1\n"
            "    mac: op://Home Lab/server-01/hardware/mac address\n"
        ))
        inv = _load(tmp_path)
        assert inv.homelab_hosts["server-01"].mac is None

    def test_bare_hex_mac_accepted(self, tmp_path, monkeypatch):
        _fake_op(monkeypatch, stdout=(
            "homelab_domain: example.com\n"
            "homelab_hosts:\n  server-01:\n    ip: 10.0.0.1\n    mac: aabbccddeeff\n"
        ))
        inv = _load(tmp_path)
        assert inv.homelab_hosts["server-01"].mac == "aabbccddeeff"


class TestSources:
    @responses.activate
    def test_github_fetch_error_wrapped(self):
        url = "https://raw.githubusercontent.com/o/r/main/p.yaml"
        responses.get(url, status=404)
        with pytest.raises(InventoryError, match="Cannot fetch inventory"):
            nhi.load(config_repo="o/r", repo_config_path="p.yaml")

    @responses.activate
    def test_github_fetch_success(self, monkeypatch):
        url = "https://raw.githubusercontent.com/o/r/main/p.yaml"
        responses.get(url, body=RESOLVED)
        inv = nhi.load(config_repo="o/r", repo_config_path="p.yaml")
        assert inv.homelab_domain == "example.com"

    def test_missing_local_file_wrapped(self, tmp_path):
        with pytest.raises(InventoryError, match="Cannot read inventory file"):
            nhi.load(local_config_path=str(tmp_path / "nope.yaml"))
