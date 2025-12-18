from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests.exceptions import ConnectionError, Timeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from logging_config import get_logger

logger = get_logger("services.amboss")

from ..storage import Storage

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0


class AmbossService:
    def __init__(self, storage: Storage, token: str, url: str = "https://api.amboss.space/graphql") -> None:
        self._storage = storage
        self._token = token
        self._url = url
        logger.info("Amboss Service inicializado")

    def _cached_series(self, pubkey: str, metric: str, submetric: str, ttl: int) -> Optional[list]:
        row = self._storage.get_amboss_series(pubkey, metric, submetric)
        if not row:
            return None
        if row["updated_at"] and ttl > 0:
            if int(time.time()) - int(row["updated_at"]) > ttl:
                return None
        return row["data"]

    def _post_with_retry(self, headers: dict, payload: dict) -> requests.Response:
        backoff = INITIAL_BACKOFF
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                return requests.post(self._url, headers=headers, json=payload, timeout=30)
            except (ConnectionError, Timeout) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                continue

        raise ConnectionError(f"Amboss API: {last_error}") from last_error

    def historical_series(
        self,
        pubkey: str,
        metric: str,
        submetric: str,
        *,
        from_date: str,
        ttl: int,
    ) -> Optional[list]:
        cached = self._cached_series(pubkey, metric, submetric, ttl)
        if cached is not None:
            logger.debug(f"Cache hit para {metric}/{submetric} pubkey={pubkey[:16]}...")
            return cached

        logger.debug(f"Buscando métricas Amboss: {metric}/{submetric} pubkey={pubkey[:16]}...")
        headers = {
            "content-type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        payload = {
            "query": """
            query GetNodeMetrics($from: String!, $metric: NodeMetricsKeys!, $pubkey: String!, $submetric: ChannelMetricsKeys) {
              getNodeMetrics(pubkey: $pubkey) {
                historical_series(from: $from, metric: $metric, submetric: $submetric)
              }
            }
            """,
            "variables": {
                "from": from_date,
                "metric": metric,
                "pubkey": pubkey,
                "submetric": submetric,
            },
        }
        try:
            resp = requests.post(self._url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            series = data["data"]["getNodeMetrics"]["historical_series"] or []
            cleaned = [float(entry[1]) for entry in series if isinstance(entry, list) and len(entry) == 2]
            self._storage.set_amboss_series(pubkey, metric, submetric, cleaned)
            logger.debug(f"Métricas Amboss obtidas: {len(cleaned)} pontos")
            return cleaned
        except KeyError as exc:
            logger.error(f"Resposta inesperada do Amboss: {data}")
            raise RuntimeError(f"Unexpected Amboss response: {data}") from exc
        except requests.RequestException as e:
            logger.error(f"Erro ao buscar métricas Amboss: {e}")
            raise

