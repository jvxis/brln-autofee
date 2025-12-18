from __future__ import annotations

import time
from typing import Optional

import requests
from requests.exceptions import ConnectionError, Timeout

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0


class TelegramService:
    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self._token = token
        self._chat_id = chat_id

    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, message: str, *, parse_mode: Optional[str] = None) -> None:
        if not self.enabled():
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for chunk in chunk_text(message, 3900):
            payload = {
                "chat_id": self._chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            self._post_with_retry(url, payload)

    def _post_with_retry(self, url: str, payload: dict) -> None:
        backoff = INITIAL_BACKOFF
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                requests.post(url, timeout=15, json=payload)
                return
            except (ConnectionError, Timeout) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                continue


def chunk_text(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks
