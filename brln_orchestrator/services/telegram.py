from __future__ import annotations

from typing import Optional

import requests


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
            requests.post(
                url,
                timeout=15,
                json=payload,
            )


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
