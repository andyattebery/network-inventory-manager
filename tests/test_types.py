import os
import pytest
import yaml
from pathlib import Path

from network_inventory_manager._types import (
    Settings,
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


class TestIsValidMac:
    def test_valid(self):
        assert is_valid_mac("aa:bb:cc:dd:ee:ff") is True

    def test_unresolved_op_ref(self):
        assert is_valid_mac("{{ op://vault/item/field }}") is False

    def test_unresolved_op_ref_no_spaces(self):
        assert is_valid_mac("{{op://vault/item/field}}") is False


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
