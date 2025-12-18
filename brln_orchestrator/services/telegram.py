from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import requests
from requests.exceptions import ConnectionError, Timeout

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from logging_config import get_logger

logger = get_logger("services.telegram")


class TelegramService:
    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self._token = token
        self._chat_id = chat_id

    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, message: str, *, parse_mode: Optional[str] = None) -> None:
        if not self.enabled():
            logger.debug("Telegram desabilitado, mensagem não enviada")
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        chunks = chunk_text(message, 3900)
        logger.debug(f"Enviando mensagem Telegram ({len(chunks)} chunks)")
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": self._chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                resp = requests.post(url, timeout=15, json=payload)
                if resp.status_code == 200:
                    logger.debug(f"Chunk {i+1}/{len(chunks)} enviado com sucesso")
                else:
                    logger.warning(f"Telegram retornou status {resp.status_code}: {resp.text[:200]}")
            except requests.RequestException as e:
                logger.error(f"Erro ao enviar mensagem Telegram: {e}")


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
