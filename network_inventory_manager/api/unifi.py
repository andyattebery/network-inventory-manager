from __future__ import annotations

import logging
from types import TracebackType

import requests
import urllib3

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class UnifiAPIClient:
    def __init__(self, url: str, username: str, password: str) -> None:
        self._session = requests.Session()
        self._session.verify = False
        try:
            resp = self._session.post(
                f"{url}/api/auth/login",
                json={"username": username, "password": password},
            )
            resp.raise_for_status()
            csrf = resp.headers.get("X-CSRF-Token") or resp.cookies.get("TOKEN", "")
            if csrf:
                self._session.headers["X-CSRF-Token"] = csrf
        except Exception:
            self._session.close()
            raise
        self._base_url = f"{url}/proxy/network"

    def __enter__(self) -> UnifiAPIClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._session.close()

    def _get(self, path: str) -> list[dict]:
        resp = self._session.get(f"{self._base_url}/{path}")
        resp.raise_for_status()
        return resp.json()["data"]

    def _post(self, path: str, data: dict) -> list[dict]:
        resp = self._session.post(f"{self._base_url}/{path}", json=data)
        resp.raise_for_status()
        return resp.json()["data"]

    def _put(self, path: str, data: dict) -> list[dict]:
        resp = self._session.put(f"{self._base_url}/{path}", json=data)
        resp.raise_for_status()
        return resp.json()["data"]

    def get_active_clients(self, site: str) -> list[dict]:
        return self._get(f"api/s/{site}/stat/sta")

    def get_configured_users(self, site: str) -> list[dict]:
        return self._get(f"api/s/{site}/rest/user")

    def create_user(self, site: str, data: dict) -> list[dict]:
        return self._post(f"api/s/{site}/rest/user", data)

    def update_user(self, site: str, user_id: str, data: dict) -> list[dict]:
        return self._put(f"api/s/{site}/rest/user/{user_id}", data)
