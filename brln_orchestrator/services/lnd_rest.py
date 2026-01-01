from __future__ import annotations

import codecs
import json
import ssl
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from logging_config import get_logger

logger = get_logger("services.lnd_rest")


class LndRestService:
    def __init__(
        self,
        rest_host: str = "localhost:8080",
        macaroon_path: Optional[str] = None,
        tls_cert_path: Optional[str] = None,
    ) -> None:
        self.rest_host = rest_host.replace("http://", "").replace("https://", "")
        self.base_url = f"https://{self.rest_host}"

        lnd_dir = Path.home() / ".lnd"
        self.macaroon_path = Path(macaroon_path) if macaroon_path else (
            lnd_dir / "data" / "chain" / "bitcoin" / "mainnet" / "admin.macaroon"
        )
        self.tls_cert_path = Path(tls_cert_path) if tls_cert_path else (
            lnd_dir / "tls.cert"
        )

        self.macaroon_hex = self._load_macaroon()

        self.session = self._create_session()
        logger.info(f"LND REST Service inicializado: {self.base_url}")

        self._chan_point_cache: Dict[str, List[str]] = {}
        self._channels_loaded = False

    def _load_macaroon(self) -> str:
        if not self.macaroon_path.exists():
            raise FileNotFoundError(f"Macaroon não encontrado: {self.macaroon_path}")

        with open(self.macaroon_path, "rb") as f:
            macaroon_bytes = f.read()

        return codecs.encode(macaroon_bytes, "hex").decode("ascii")

    def _create_session(self) -> requests.Session:
        session = requests.Session()

        session.headers.update({
            "Grpc-Metadata-macaroon": self.macaroon_hex,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        })

        session.verify = str(self.tls_cert_path)

        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=1,
            pool_maxsize=1,
            pool_block=True,
        )
        session.mount("https://", adapter)

        return session

    def _load_channels(self) -> None:
        if self._channels_loaded:
            return

        url = f"{self.base_url}/v1/channels"
        logger.debug("Carregando lista de canais")
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            channel_count = 0
            for channel in data.get("channels", []):
                pubkey = channel.get("remote_pubkey")
                chan_point = channel.get("channel_point")
                if pubkey and chan_point:
                    bucket = self._chan_point_cache.setdefault(pubkey, [])
                    if chan_point not in bucket:
                        bucket.append(chan_point)
                        channel_count += 1

            self._channels_loaded = True
            logger.info(f"Canais carregados: {channel_count} canais ({len(self._chan_point_cache)} peers)")
        except requests.exceptions.RequestException as exc:
            logger.error(f"Erro ao listar canais: {exc}")
            raise RuntimeError(f"Erro ao listar canais: {exc}") from exc

    def _get_chan_points_for_pubkey(self, pubkey: str) -> List[str]:
        if not self._channels_loaded:
            self._load_channels()
        return list(self._chan_point_cache.get(pubkey, []))

    def _get_chan_point_for_pubkey(self, pubkey: str) -> Optional[str]:
        points = self._get_chan_points_for_pubkey(pubkey)
        return points[0] if points else None

    def refresh_channels(self) -> None:
        self._channels_loaded = False
        self._chan_point_cache.clear()
        self._load_channels()

    def _build_policy_payload(
        self,
        chan_point: str,
        ppm: int,
        inbound_discount_ppm: Optional[int],
    ) -> Dict[str, Any]:
        parts = chan_point.split(":")
        if len(parts) != 2:
            raise ValueError(f"chan_point invalido: {chan_point}")

        data: Dict[str, Any] = {
            "chan_point": {
                "funding_txid_str": parts[0],
                "output_index": int(parts[1]),
            },
            "fee_rate_ppm": max(0, int(ppm)),
            "time_lock_delta": 80,
        }

        if inbound_discount_ppm is not None:
            data["inbound_fee"] = {
                "fee_rate_ppm": -max(0, int(inbound_discount_ppm))
            }
        return data

    def set_fee_by_chan_point(
        self,
        chan_point: str,
        ppm: int,
        *,
        inbound_discount_ppm: Optional[int] = None,
        dry_run: bool = False,
    ) -> Optional[str]:
        if dry_run:
            msg = f"[dry-run] REST update chan_point={chan_point[:16]}... fee_rate_ppm={ppm}"
            if inbound_discount_ppm is not None:
                msg += f" inbound_fee_ppm={-inbound_discount_ppm}"
            return msg

        data = self._build_policy_payload(chan_point, ppm, inbound_discount_ppm)
        try:
            url = f"{self.base_url}/v1/chanpolicy"
            logger.debug(f"Atualizando fee: chan_point={chan_point[:16]}... ppm={ppm}")
            response = self.session.post(url, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()

            failed = result.get("failed_updates", [])
            if failed:
                errors = [f"{f.get('outpoint', '?')}: {f.get('update_error', 'unknown')}" for f in failed]
                logger.error(f"Falha ao atualizar politica: {', '.join(errors)}")
                raise RuntimeError(f"Falha ao atualizar politica: {', '.join(errors)}")

            logger.info(f"Fee atualizado: chan_point={chan_point[:16]}... ppm={ppm}")
            return None

        except requests.exceptions.RequestException as exc:
            logger.error(f"REST API error: {exc}")
            raise RuntimeError(f"REST API error: {exc}") from exc

    def set_fee(
        self,
        pubkey: str,
        ppm: int,
        *,
        inbound_discount_ppm: Optional[int] = None,
        dry_run: bool = False,
    ) -> Optional[str]:
        chan_points = self._get_chan_points_for_pubkey(pubkey)
        if not chan_points:
            raise RuntimeError(f"Canal nao encontrado para pubkey: {pubkey}")

        if dry_run:
            messages = []
            for chan_point in chan_points:
                msg = self.set_fee_by_chan_point(
                    chan_point,
                    ppm,
                    inbound_discount_ppm=inbound_discount_ppm,
                    dry_run=True,
                )
                if msg:
                    messages.append(msg)
            return "\n".join(messages) if messages else None

        for chan_point in chan_points:
            self.set_fee_by_chan_point(
                chan_point,
                ppm,
                inbound_discount_ppm=inbound_discount_ppm,
                dry_run=False,
            )
        return None

    def get_info(self) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/getinfo"
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Erro ao obter info: {exc}") from exc

    def list_channels(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/v1/channels"
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("channels", [])
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Erro ao listar canais: {exc}") from exc

    def close(self) -> None:
        if self.session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
