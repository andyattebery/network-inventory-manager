from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class HostEntry:
    ip: str
    mac: str | None = None
    skip_dhcp: bool = False


@dataclass
class OtherHost:
    hostname: str
    ip: str


@dataclass
class ServiceEntry:
    hostname: str


@dataclass
class NetworkHostsInventory:
    homelab_domain: str
    homelab_hosts: dict[str, HostEntry]
    other_hosts: list[OtherHost]
    services: dict[str, ServiceEntry] = None

    def __post_init__(self) -> None:
        if self.services is None:
            self.services = {}


@dataclass
class DsmService:
    name: str
    url: str
    hostname: str


@dataclass
class Rewrite:
    domain: str
    answer: str


@dataclass
class Client:
    name: str
    ids: list[str]


@dataclass
class DHCPReservation:
    name: str
    mac: str
    ip: str


@dataclass
class DesiredState:
    rewrites: list[Rewrite]
    clients: list[Client]
    dhcp_reservations: list[DHCPReservation]


@dataclass
class DiscoveredClient:
    name: str
    ip: str
    mac: str


_UNRESOLVED_OP_RE = re.compile(r"\{\{\s*op://")


def normalize_mac(mac: str) -> str:
    return mac.lower().replace("-", ":")


def is_valid_mac(value: str) -> bool:
    return not _UNRESOLVED_OP_RE.search(value)


_SETTINGS_ENV_MAP = {
    "op_service_account_token": "OP_SERVICE_ACCOUNT_TOKEN",
    "github_token": "GITHUB_TOKEN",
    "config_repo": "CONFIG_REPO",
    "repo_config_path": "CONFIG_PATH",
    "config_branch": "CONFIG_BRANCH",
    "local_config_path": "LOCAL_CONFIG_PATH",
    "dsm_url": "DSM_URL",
    "adguardhome_url": "ADGUARDHOME_URL",
    "adguardhome_username": "ADGUARDHOME_USERNAME",
    "adguardhome_password": "ADGUARDHOME_PASSWORD",
    "unifi_url": "UNIFI_URL",
    "unifi_username": "UNIFI_USERNAME",
    "unifi_password": "UNIFI_PASSWORD",
    "unifi_site": "UNIFI_SITE",
    "outputs": "OUTPUTS",
    "sync_interval": "SYNC_INTERVAL",
    "verbose": "VERBOSE",
    "port": "PORT",
}

_SETTINGS_REQUIRED = {
    "dsm_url",
    "adguardhome_url", "adguardhome_username", "adguardhome_password",
    "unifi_url", "unifi_username", "unifi_password",
}


@dataclass(frozen=True)
class Settings:
    dsm_url: str
    adguardhome_url: str
    adguardhome_username: str
    adguardhome_password: str
    unifi_url: str
    unifi_username: str
    unifi_password: str
    op_service_account_token: str = ""
    config_repo: str = ""
    repo_config_path: str = ""
    local_config_path: str = ""
    github_token: str = ""
    config_branch: str = "main"
    unifi_site: str = "default"
    outputs: tuple[str, ...] = ("adguardhome", "unifi")
    sync_interval: int = 0
    verbose: bool = False
    port: int = 8080

    @classmethod
    def load(cls, settings_path: Path | None = None) -> Settings:
        file_values: dict[str, str] = {}
        if settings_path and settings_path.is_file():
            with open(settings_path) as f:
                raw = yaml.safe_load(f) or {}
            for key in _SETTINGS_ENV_MAP:
                if key in raw and raw[key] is not None:
                    file_values[key] = str(raw[key])

        merged: dict[str, str] = {}
        for field_name, env_key in _SETTINGS_ENV_MAP.items():
            env_val = os.environ.get(env_key)
            if env_val is not None:
                merged[field_name] = env_val
            elif field_name in file_values:
                merged[field_name] = file_values[field_name]

        missing = _SETTINGS_REQUIRED - set(merged)
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(sorted(missing))}")

        has_local = bool(merged.get("local_config_path"))
        has_repo = bool(merged.get("config_repo") and merged.get("repo_config_path"))
        if not has_local and not has_repo:
            raise ValueError("Must set either local_config_path or both config_repo + repo_config_path")

        outputs_raw = merged.get("outputs", "adguardhome,unifi")
        return cls(
            dsm_url=merged["dsm_url"],
            adguardhome_url=merged["adguardhome_url"],
            adguardhome_username=merged["adguardhome_username"],
            adguardhome_password=merged["adguardhome_password"],
            unifi_url=merged["unifi_url"],
            unifi_username=merged["unifi_username"],
            unifi_password=merged["unifi_password"],
            op_service_account_token=merged.get("op_service_account_token", ""),
            config_repo=merged.get("config_repo", ""),
            repo_config_path=merged.get("repo_config_path", ""),
            local_config_path=merged.get("local_config_path", ""),
            github_token=merged.get("github_token", ""),
            config_branch=merged.get("config_branch", "main"),
            unifi_site=merged.get("unifi_site", "default"),
            outputs=tuple(s.strip() for s in outputs_raw.split(",")),
            sync_interval=int(merged.get("sync_interval", "0")),
            verbose=merged.get("verbose", "false").lower() == "true",
            port=int(merged.get("port", "8080")),
        )
