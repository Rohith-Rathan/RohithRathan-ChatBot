"""Client for the Trainer Central dashboard API (see openapi.json: POST /api/entries)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

TEXT_MAX = 10_000
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DashboardError(RuntimeError):
    pass


class DashboardClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: int = 90, retries: int = 4):
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if api_key:
            self.session.headers["x-api-key"] = api_key

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                    log.warning("%s %s -> HTTP %s (attempt %d), retrying", method, path, resp.status_code, attempt)
                else:
                    return resp
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                log.warning("%s %s failed (attempt %d): %s", method, path, attempt, e)
            time.sleep(min(5 * 2 ** (attempt - 1), 60))
        raise DashboardError(f"{method} {path} failed after {self.retries} attempts: {last_exc}")

    def wake_up(self) -> None:
        """Render free tier sleeps when idle; /health wakes it."""
        resp = self._request("GET", "/health")
        if resp.status_code != 200:
            raise DashboardError(f"Health check returned HTTP {resp.status_code}: {resp.text[:200]}")

    def find_existing(self, marker: str, sender: str) -> dict | None:
        """Return an existing entry from `sender` whose text contains `marker`, if any."""
        resp = self._request("GET", "/api/entries", params={"q": marker, "limit": 100})
        if resp.status_code != 200:
            raise DashboardError(f"GET /api/entries returned HTTP {resp.status_code}: {resp.text[:200]}")
        for item in resp.json().get("items", []):
            if item.get("sender") == sender and marker in (item.get("text") or ""):
                return item
        return None

    def post_entry(self, text: str, sender: str) -> dict:
        payload = {"text": text[:TEXT_MAX], "sender": sender}
        resp = self._request("POST", "/api/entries", json=payload)
        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except ValueError:
                return {}
        if resp.status_code in (401, 403):
            raise DashboardError(
                f"HTTP {resp.status_code}: API rejected the request; set DASHBOARD_API_KEY in .env. {resp.text[:200]}"
            )
        if resp.status_code == 422:
            raise DashboardError(f"HTTP 422 validation error: {resp.text[:500]}")
        raise DashboardError(f"POST /api/entries returned HTTP {resp.status_code}: {resp.text[:300]}")
