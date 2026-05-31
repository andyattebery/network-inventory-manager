from __future__ import annotations

import logging
import re
import subprocess
import tempfile

import os

import requests
import yaml

from network_inventory_manager._types import HostEntry, NetworkHostsInventory, OtherHost, ServiceEntry, is_valid_mac

logger = logging.getLogger(__name__)

_OP_REF_RE = re.compile(r"(:\s*)((\{\{[^}]+\}\}))(\s*)$", re.MULTILINE)


def _quote_op_refs(text: str) -> str:
    return _OP_REF_RE.sub(r'\1"\2"\4', text)


def load(
    *,
    local_config_path: str = "",
    config_repo: str = "",
    repo_config_path: str = "",
    config_branch: str = "main",
    github_token: str = "",
    op_service_account_token: str = "",
) -> NetworkHostsInventory:
    if local_config_path:
        with open(local_config_path) as f:
            template_text = f.read()
    else:
        url = f"https://raw.githubusercontent.com/{config_repo}/{config_branch}/{repo_config_path}"
        headers = {}
        if github_token:
            headers["Authorization"] = f"token {github_token}"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        template_text = resp.text

    resolved_text = _resolve_template(template_text, op_service_account_token)
    raw = yaml.safe_load(resolved_text)

    homelab_hosts: dict[str, HostEntry] = {}
    for name, entry in (raw.get("homelab_hosts") or {}).items():
        mac = entry.get("mac")
        if mac and not is_valid_mac(str(mac)):
            logger.warning("Unresolved 1Password reference for host %s, skipping MAC", name)
            mac = None
        homelab_hosts[name] = HostEntry(ip=entry["ip"], mac=mac, skip_dhcp=bool(entry.get("skip_dhcp")))

    other_hosts = [
        OtherHost(hostname=h["hostname"], ip=h["ip"])
        for h in (raw.get("other_hosts") or [])
    ]

    services = {
        name: ServiceEntry(hostname=entry["hostname"])
        for name, entry in (raw.get("services") or {}).items()
    }

    return NetworkHostsInventory(
        homelab_domain=raw["homelab_domain"],
        homelab_hosts=homelab_hosts,
        other_hosts=other_hosts,
        services=services,
    )


def _resolve_template(template_text: str, op_service_account_token: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml.tpl", delete_on_close=False
    ) as f:
        f.write(template_text)
        f.flush()
        env = None
        if op_service_account_token:
            env = {**os.environ, "OP_SERVICE_ACCOUNT_TOKEN": op_service_account_token}
        result = subprocess.run(
            ["op", "inject", "-i", f.name],
            capture_output=True,
            text=True,
            env=env,
        )

    if result.returncode == 0:
        return result.stdout

    logger.warning("op inject failed (rc=%d): %s", result.returncode, result.stderr.strip())
    return _quote_op_refs(template_text)
