import os
import pytest
import yaml
from pathlib import Path

from network_inventory_manager._types import (
    Settings,
    has_unresolved_op_ref,
    is_valid_dns_name,
    is_valid_ip,
    is_valid_mac,
    normalize_mac,
)


class TestNormalizeMac:
    def test_lowercase(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"

    def test_dash_to_colon(self):
        assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"

    def test_already_normalized(self):
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_mixed(self):
        assert normalize_mac("AA-BB-CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"


class TestHasUnresolvedOpRef:
    def test_resolved_value(self):
        assert has_unresolved_op_ref("aa:bb:cc:dd:ee:ff") is False

    def test_unresolved_op_ref(self):
        assert has_unresolved_op_ref("{{ op://vault/item/field }}") is True

    def test_unresolved_op_ref_no_spaces(self):
        assert has_unresolved_op_ref("{{op://vault/item/field}}") is True

    def test_embedded_in_larger_text(self):
        assert has_unresolved_op_ref("domain: {{ op://v/i/f }}\nhosts: {}") is True

    def test_non_string(self):
        assert has_unresolved_op_ref(None) is False


class TestIsValidMac:
    def test_colon_separated(self):
        assert is_valid_mac("aa:bb:cc:dd:ee:ff") is True

    def test_dash_separated_uppercase(self):
        assert is_valid_mac("AA-BB-CC-DD-EE-FF") is True

    def test_bare_hex(self):
        assert is_valid_mac("aabbccddeeff") is True

    def test_dot_separated(self):
        assert is_valid_mac("aa.bb.cc.dd.ee.ff") is True

    def test_non_hex_digit(self):
        assert is_valid_mac("zz:bb:cc:dd:ee:ff") is False

    def test_too_short(self):
        assert is_valid_mac("aa:bb:cc") is False

    def test_unresolved_op_ref(self):
        assert is_valid_mac("{{ op://vault/item/field }}") is False

    def test_mangled_op_ref(self):
        assert is_valid_mac("op://vault/item/field") is False

    def test_non_string(self):
        assert is_valid_mac(None) is False


class TestIsValidDnsName:
    def test_domain(self):
        assert is_valid_dns_name("example.com") is True

    def test_bare_label(self):
        # A homelab may use `lan` or `home` as its TLD.
        assert is_valid_dns_name("lan") is True

    def test_dotted_host_key(self):
        assert is_valid_dns_name("bmc.nas-host-01") is True

    def test_hyphenated_service_key(self):
        assert is_valid_dns_name("llama-swap.htpc-01") is True

    def test_unresolved_op_ref(self):
        assert is_valid_dns_name("{{ op://Personal/Home Lab/domains/internal }}") is False

    def test_lowercased_unresolved_op_ref(self):
        # The shape AdGuardHome stored during the incident.
        assert is_valid_dns_name("{{ op://personal/home lab/domains/internal }}") is False

    def test_mangled_op_ref(self):
        assert is_valid_dns_name("op://Personal/Home Lab/domains/internal") is False

    def test_empty(self):
        assert is_valid_dns_name("") is False

    def test_leading_hyphen(self):
        assert is_valid_dns_name("-bad.com") is False

    def test_leading_dot(self):
        assert is_valid_dns_name(".com") is False

    def test_embedded_space(self):
        assert is_valid_dns_name("a b.com") is False

    def test_non_string(self):
        assert is_valid_dns_name(None) is False


class TestIsValidIp:
    def test_ipv4(self):
        assert is_valid_ip("192.168.1.1") is True

    def test_ipv6(self):
        assert is_valid_ip("::1") is True

    def test_not_an_ip(self):
        assert is_valid_ip("not-an-ip") is False

    def test_empty(self):
        assert is_valid_ip("") is False

    def test_non_string(self):
        assert is_valid_ip(None) is False

    def test_yaml_float(self):
        # `ip: 10.0` parses as a float, not a string.
        assert is_valid_ip(10.0) is False


class TestSettingsLoad:
    def _write_yaml(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "settings.yaml"
        p.write_text(yaml.dump(data))
        return p

    def _base_settings(self) -> dict:
        return {
            "dsm_url": "https://dsm.example.com",
            "adguardhome_url": "https://adguard.example.com",
            "adguardhome_username": "admin",
            "adguardhome_password": "pass",
            "unifi_url": "https://192.168.1.1",
            "unifi_username": "admin",
            "unifi_password": "pass",
            "local_config_path": "/config/hosts.yaml",
        }

    def test_load_from_yaml(self, tmp_path):
        path = self._write_yaml(tmp_path, self._base_settings())
        settings = Settings.load(path)
        assert settings.dsm_url == "https://dsm.example.com"
        assert settings.unifi_site == "default"

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        path = self._write_yaml(tmp_path, self._base_settings())
        monkeypatch.setenv("DSM_URL", "https://override.example.com")
        settings = Settings.load(path)
        assert settings.dsm_url == "https://override.example.com"

    def test_missing_required_raises(self, tmp_path):
        path = self._write_yaml(tmp_path, {"local_config_path": "/config/hosts.yaml"})
        with pytest.raises(ValueError, match="Missing required settings"):
            Settings.load(path)

    def test_missing_config_source_raises(self, tmp_path):
        data = self._base_settings()
        del data["local_config_path"]
        path = self._write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="local_config_path"):
            Settings.load(path)

    def test_outputs_parsing(self, tmp_path):
        data = self._base_settings()
        data["outputs"] = "adguardhome"
        path = self._write_yaml(tmp_path, data)
        assert Settings.load(path).outputs == ("adguardhome",)

    def test_removal_grace_cycles_default(self, tmp_path):
        path = self._write_yaml(tmp_path, self._base_settings())
        assert Settings.load(path).removal_grace_cycles == 8

    def test_removal_grace_cycles_from_yaml(self, tmp_path):
        data = self._base_settings()
        data["removal_grace_cycles"] = 3
        path = self._write_yaml(tmp_path, data)
        assert Settings.load(path).removal_grace_cycles == 3

    def test_removal_grace_cycles_from_env(self, tmp_path, monkeypatch):
        path = self._write_yaml(tmp_path, self._base_settings())
        monkeypatch.setenv("REMOVAL_GRACE_CYCLES", "0")
        assert Settings.load(path).removal_grace_cycles == 0
