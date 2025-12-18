from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, TypeVar

import requests
from requests.auth import HTTPBasicAuth
from requests.exceptions import ConnectionError, Timeout

T = TypeVar("T")

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0


def _with_retry(func: Callable[[], T], operation: str) -> T:
    last_error: Optional[Exception] = None
    backoff = INITIAL_BACKOFF

    for attempt in range(MAX_RETRIES):
        try:
            return func()
        except (ConnectionError, Timeout) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
            continue

    raise ConnectionError(f"{operation}: {last_error}") from last_error


class LNDgAPI:
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str]) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(username, password) if username and password else None

    def list_channels(self) -> list[Dict[str, Any]]:
        url = f"{self._base_url}/api/channels/"
        params: Dict[str, str] = {"is_open": "true", "is_active": "true"}
        results: list[Dict[str, Any]] = []

        while url:
            def fetch() -> requests.Response:
                return requests.get(url, params=params, auth=self._auth, timeout=20)

            resp = _with_retry(fetch, f"list_channels GET {url}")
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "results" in data:
                results.extend(data["results"])
                url = data.get("next")
                params = {}  # already encoded in next
            elif isinstance(data, list):
                results.extend(data)
                url = None
            else:
                raise RuntimeError(f"Unexpected LNDg response: {data}")
        return results

    def update_channel(self, chan_id: str, payload: Dict[str, Any]) -> None:
        url = f"{self._base_url}/api/channels/{chan_id}/"

        def put_request() -> requests.Response:
            return requests.put(url, json=payload, auth=self._auth, timeout=20)

        resp = _with_retry(put_request, f"update_channel PUT {url}")

        if 200 <= resp.status_code < 300:
            return
        if resp.status_code in (400, 405):
            def patch_request() -> requests.Response:
                return requests.patch(url, json=payload, auth=self._auth, timeout=20)

            resp = _with_retry(patch_request, f"update_channel PATCH {url}")
            resp.raise_for_status()
            return
        # Preserve original error details for other status codes
        resp.raise_for_status()
