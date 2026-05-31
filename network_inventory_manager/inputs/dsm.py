from __future__ import annotations

import logging

import requests

from network_inventory_manager._types import DsmService

logger = logging.getLogger(__name__)


def fetch_services(dsm_url: str) -> list[DsmService]:
    resp = requests.get(f"{dsm_url}/dashboard-services")
    resp.raise_for_status()
    return [
        DsmService(name=s["name"], url=s["url"], hostname=s["hostname"])
        for s in resp.json()
    ]
