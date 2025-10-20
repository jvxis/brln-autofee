from __future__ import annotations

from typing import Any, Dict, Optional

import requests
from requests.auth import HTTPBasicAuth


class LNDgAPI:
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str]) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(username, password) if username and password else None

    def list_channels(self) -> list[Dict[str, Any]]:
        url = f"{self._base_url}/api/channels/"
        params = {"is_open": "true", "is_active": "true"}
        results = []
        while url:
            resp = requests.get(url, params=params, auth=self._auth, timeout=20)
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
        resp = requests.put(url, json=payload, auth=self._auth, timeout=20)
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code in (400, 405):
            resp = requests.patch(url, json=payload, auth=self._auth, timeout=20)
            resp.raise_for_status()
            return
        # Preserve original error details for other status codes
        resp.raise_for_status()
