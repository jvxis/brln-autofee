from __future__ import annotations

import sys
import time
from pathlib import Path
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


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from logging_config import get_logger

logger = get_logger("services.lndg_api")


class LNDgAPI:
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str]) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(username, password) if username and password else None
        logger.info(f"LNDg API inicializada: {self._base_url}")

    def list_channels(self) -> list[Dict[str, Any]]:
        url = f"{self._base_url}/api/channels/"
        params = {"is_open": "true", "is_active": "true"}
        results = []
        logger.debug("Listando canais do LNDg")
        while url:
            try:
                resp = requests.get(url, params=params, auth=self._auth, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "results" in data:
                    results.extend(data["results"])
                    url = data.get("next")
                    params = {}
                elif isinstance(data, list):
                    results.extend(data)
                    url = None
                else:
                    logger.error(f"Resposta inesperada do LNDg: {data}")
                    raise RuntimeError(f"Unexpected LNDg response: {data}")
            except requests.RequestException as e:
                logger.error(f"Erro ao listar canais do LNDg: {e}")
                raise
        logger.debug(f"Canais listados: {len(results)} canais")
        return results

    def update_channel(self, chan_id: str, payload: Dict[str, Any]) -> None:
        url = f"{self._base_url}/api/channels/{chan_id}/"
        logger.debug(f"Atualizando canal {chan_id}: {payload}")
        try:
            resp = requests.put(url, json=payload, auth=self._auth, timeout=20)
            if 200 <= resp.status_code < 300:
                logger.info(f"Canal {chan_id} atualizado com sucesso")
                return
            if resp.status_code in (400, 405):
                resp = requests.patch(url, json=payload, auth=self._auth, timeout=20)
                resp.raise_for_status()
                logger.info(f"Canal {chan_id} atualizado via PATCH")
                return
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Erro ao atualizar canal {chan_id}: {e}")
            raise
