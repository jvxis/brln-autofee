from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sqlite3
import string
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..services.amboss import AmbossService
from ..services.bos import BosService
from ..services.lnd_rest import LndRestService
from ..services.lndg_db import LNDgDatabase
from ..services.lncli import LncliService
from ..services.telegram import TelegramService

FeeService = BosService | LndRestService
from ..presets import get_mode_presets
from ..storage import Storage


def _load_legacy(path: Path):
    spec = importlib.util.spec_from_file_location("legacy_autofee", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    spec.loader.exec_module(module)
    return module


SYMPTOM_KEYS = ("floor_lock", "no_down_low", "hold_small", "cb_trigger", "discovery")
SYMPTOM_HEADER_RE = re.compile(r"(?:DRY[-\s]*RUN\s*)?[\u2699\uFE0F\u200D\uFE0F]*\s*AutoFee\s*\|\s*janela\s*\d+d", re.IGNORECASE)
SYMPTOM_DICT_RE = re.compile(r"Symptoms:\s*\{([^}]*)\}", re.IGNORECASE)
SYMPTOM_TOKEN_PATTERNS = {
    "floor_lock": re.compile(r"floor[-_\s]*lock", re.IGNORECASE),
    "no_down_low": re.compile(r"no[-_\s]*down[-_\s]*low", re.IGNORECASE),
    "hold_small": re.compile(r"hold[-_\s]*small", re.IGNORECASE),
    "cb_trigger": re.compile(r"(?:cb[-_\s]*trigger|cb\s*[:=])", re.IGNORECASE),
    "discovery": re.compile(r"discovery", re.IGNORECASE),
}


def _extract_symptoms_from_text(text: Optional[str]) -> Optional[Dict[str, int]]:
    if not text:
        return None
    block = str(text)
    hits = list(SYMPTOM_HEADER_RE.finditer(block))
    if hits:
        block = block[hits[-1].start():]
    counts = {key: 0 for key in SYMPTOM_KEYS}
    found = False
    match = SYMPTOM_DICT_RE.search(block)
    if match:
        payload = "{" + match.group(1) + "}"
        payload = payload.replace("'", '"')
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            for key in SYMPTOM_KEYS:
                if key in parsed:
                    value = parsed.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        counts[key] = int(value)
                        found = True
                    elif isinstance(value, str):
                        value = value.strip()
                        if value.isdigit():
                            counts[key] = int(value)
                            found = True
    for key, pattern in SYMPTOM_TOKEN_PATTERNS.items():
        matches = pattern.findall(block)
        if matches:
            counts[key] += len(matches)
            found = True
    return counts if found else None


class AutoFeeEngine:
    def __init__(
        self,
        storage: Storage,
        lncli: LncliService,
        bos: FeeService,
        amboss: Optional[AmbossService],
        telegram: TelegramService,
        legacy_path: Path,
    ) -> None:
        self.storage = storage
        self.lncli = lncli
        self.bos = bos
        self.amboss = amboss
        self.telegram = telegram
        self.legacy = _load_legacy(legacy_path)

    # ------------------------------------------------------------------ #
    # Helpers injected into legacy module
    # ------------------------------------------------------------------ #

    def _store_last_symptoms(self, text: str) -> None:
        counts = _extract_symptoms_from_text(text)
        if counts is None:
            return
        try:
            self.storage.save_json("legacy_autofee_last_symptoms", counts)
        except Exception:
            pass

    def _load_json(self, name: str, default: Any) -> Any:
        if name == self.legacy.CACHE_PATH:
            return self.storage.load_autofee_cache()
        if name == self.legacy.STATE_PATH:
            return self.storage.load_autofee_state()
        if name == self.legacy.OVERRIDES_PATH:
            return self.storage.load_overrides("autofee")
        return self.storage.load_json(name, default)

    def _save_json(self, name: str, data: Any) -> None:
        if name == self.legacy.CACHE_PATH:
            self.storage.save_autofee_cache(data)
        elif name == self.legacy.STATE_PATH:
            self.storage.save_autofee_state(data)
        elif name == self.legacy.OVERRIDES_PATH:
            self.storage.save_overrides("autofee", data)
        else:
            self.storage.save_json(name, data)

    def _db_connect(self) -> sqlite3.Connection:
        secrets = self.storage.get_secrets()
        db_path = secrets.get("lndg_db_path")
        if not db_path:
            raise RuntimeError("LNDg database path not configured (set-secret --lndg-db-path ...)")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _lncli_listchannels(self) -> Dict[str, Any]:
        return self.lncli.listchannels()

    def _listchannels_snapshot(self):
        data = self._lncli_listchannels()
        if not isinstance(data, dict):
            raise RuntimeError("lncli listchannels returned invalid payload")
        by_scid_dec: Dict[str, Any] = {}
        by_cid_dec: Dict[str, Any] = {}
        by_point: Dict[str, Any] = {}
        for ch in data.get("channels", []):
            scid = ch.get("scid")
            cid = ch.get("chan_id")
            point = ch.get("channel_point")
            info = {
                "capacity": int(ch.get("capacity", 0)),
                "local_balance": int(ch.get("local_balance", 0)),
                "remote_balance": int(ch.get("remote_balance", 0)),
                "remote_pubkey": ch.get("remote_pubkey"),
                "chan_point": point,
                "active": bool(ch.get("active", False)),
                "initiator": ch.get("initiator"),
            }
            if scid is not None and str(scid).isdigit():
                by_scid_dec[str(scid)] = info
            if cid is not None and str(cid).isdigit():
                by_cid_dec[str(cid)] = info
            if point:
                by_point[point] = info
        return {"by_scid_dec": by_scid_dec, "by_cid_dec": by_cid_dec, "by_point": by_point}

    def _bos_set_fees(self, pubkey: str, ppm: int, inbound_discount_ppm: Optional[int], dry_run: bool) -> None:
        self.bos.set_fee(pubkey, ppm, inbound_discount_ppm=inbound_discount_ppm, dry_run=dry_run)

    def _bos_set_fee(self, pubkey: str, ppm: int, dry_run: bool) -> None:
        self._bos_set_fees(pubkey, ppm, None, dry_run)

    def _tg_send(self, text: str) -> None:
        if not text:
            return
        if self.telegram.enabled():
            self.telegram.send(text)

    def _read_version_info(self, _path: str) -> Dict[str, str]:
        version = self.storage.get_meta("app_version", "0.0.0")
        desc = self.storage.get_meta("app_version_desc", "")
        return {"version": version or "0.0.0", "desc": desc or ""}

    def _run_command(self, cmd: str) -> str:
        cmd = cmd.strip()
        if "listchannels" in cmd:
            return json.dumps(self._lncli_listchannels())
        raise RuntimeError(f"Unsupported command: {cmd}")

    # ------------------------------------------------------------------ #

    def run(self, *, dry_run: bool, mode: str = "conservador", didactic_explain: bool, didactic_detailed: bool) -> str:
        """Execute the legacy AutoFee main, capturing stdout."""
        legacy = self.legacy

        self._apply_mode_presets(mode or "conservador", legacy)

        # Configure secrets
        secrets = self.storage.get_secrets()
        legacy.DB_PATH = secrets.get("lndg_db_path") or ""
        legacy.AMBOSS_TOKEN = secrets.get("amboss_token") or ""
        legacy.TELEGRAM_TOKEN = secrets.get("telegram_token") or ""
        legacy.TELEGRAM_CHAT = secrets.get("telegram_chat") or ""
        legacy.LNCLI = secrets.get("lncli_path") or "lncli"
        legacy.BOS = secrets.get("bos_path") or "bos"

        # Map storage-backed paths
        legacy.CACHE_PATH = "legacy_autofee_cache"
        legacy.STATE_PATH = "legacy_autofee_state"
        legacy.OVERRIDES_PATH = "legacy_autofee_overrides"

        def _load_overrides() -> bool:
            try:
                overrides = self.storage.load_overrides("autofee")
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[autofee] erro ao carregar overrides: {exc}", file=sys.stderr)
                return False
            if not overrides:
                return False
            try:
                legacy._apply_overrides(legacy.__dict__, overrides)
                return True
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[autofee] overrides invalidos: {exc}", file=sys.stderr)
                return False

        # Exclusions
        raw_exclusions = self.storage.list_exclusions()
        normalized_exclusions: Dict[str, str] = {}
        for identifier, note in raw_exclusions.items():
            norm = self._normalize_identifier(identifier)
            if not norm:
                continue
            normalized_exclusions[norm] = note or ""

        # original script expects dict<pubkey, note>, but alguns identificadores podem ser channel ids.
        legacy.EXCLUSION_LIST = dict(normalized_exclusions)

        prelude: List[str] = []
        if dry_run:
            pubkey_exclusions = self._pubkey_exclusions(normalized_exclusions)
            if pubkey_exclusions:
                prelude.append(f"[dry-run] AutoFee ignorando {len(pubkey_exclusions)} pubkey(s) por exclusao:")
                for identifier, note in sorted(pubkey_exclusions):
                    suffix = f" ({note})" if note else ""
                    prelude.append(f"  - {identifier}{suffix}")

        # Provide hooks
        legacy.load_json = self._load_json  # type: ignore
        legacy.save_json = self._save_json  # type: ignore
        legacy.db_connect = self._db_connect  # type: ignore
        legacy.listchannels_snapshot = self._listchannels_snapshot  # type: ignore
        legacy.bos_set_fees = lambda pubkey, ppm_value, inbound_discount_ppm=None: self._bos_set_fees(pubkey, ppm_value, inbound_discount_ppm, dry_run)  # type: ignore
        legacy.bos_set_fee_ppm = lambda pubkey, ppm_value: self._bos_set_fees(pubkey, ppm_value, None, dry_run)  # type: ignore
        legacy.tg_send_big = self._tg_send  # type: ignore
        legacy.read_version_info = self._read_version_info  # type: ignore
        legacy.run = self._run_command  # type: ignore
        legacy.load_overrides = _load_overrides  # type: ignore
        legacy.load_overrides()

        # Fallback for LNDg schema differences
        lndg_db_path = secrets.get("lndg_db_path")
        if lndg_db_path:
            db_helper = LNDgDatabase(lndg_db_path)
            if not db_helper.table_exists("gui_payments") and db_helper.table_exists("payments"):
                legacy.SQL_REBAL_PAYMENTS = legacy.SQL_REBAL_PAYMENTS.replace("FROM gui_payments", "FROM payments")
            if not db_helper.table_exists("gui_forwards") and db_helper.table_exists("forwards"):
                legacy.SQL_FORWARDS = legacy.SQL_FORWARDS.replace("FROM gui_forwards", "FROM forwards")

        # Didactic flags
        legacy.DIDACTIC_EXPLAIN_ENABLE = didactic_explain or didactic_detailed
        legacy.DIDACTIC_LEVEL = "detailed" if didactic_detailed else ("basic" if didactic_explain else legacy.DIDACTIC_LEVEL)

        # Amboss service replacement (optional)
        if self.amboss:
            cache_ttl = int(getattr(legacy, "AMBOSS_CACHE_TTL_SEC", 3 * 3600))
            lookback_days = int(getattr(legacy, "LOOKBACK_DAYS", 7))

            def _amboss_seed_series_7d(pubkey: str, cache: Dict[str, Any]):
                if not pubkey:
                    return None
                key = f"incoming_series_7d:{pubkey}"
                now = int(legacy.time.time())
                cache_dict = cache if isinstance(cache, dict) else None
                if cache_dict:
                    entry = cache_dict.get(key) or {}
                    ts = entry.get("ts")
                    if ts and now - int(ts) < cache_ttl:
                        return entry.get("vals")
                from_date = (legacy.now_utc() - legacy.datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                try:
                    series = self.amboss.historical_series(
                        pubkey,
                        "incoming_fee_rate_metrics",
                        "weighted_corrected_mean",
                        from_date=from_date,
                        ttl=cache_ttl,
                    ) or []
                except Exception:
                    return None
                vals = [float(v) for v in series if v is not None]
                if not vals:
                    return None
                if cache_dict is not None:
                    cache_dict[key] = {"ts": now, "vals": vals}
                return vals

            def _amboss_series_generic(pubkey: str, metric: str, submetric: str, cache: Dict[str, Any]):
                if not pubkey:
                    return []
                key = f"series7d:{metric}:{submetric}:{pubkey}"
                now = int(legacy.time.time())
                cache_dict = cache if isinstance(cache, dict) else None
                if cache_dict:
                    entry = cache_dict.get(key) or {}
                    ts = entry.get("ts")
                    if ts and now - int(ts) < cache_ttl:
                        return entry.get("vals") or []
                from_date = (legacy.now_utc() - legacy.datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                try:
                    series = self.amboss.historical_series(
                        pubkey,
                        metric,
                        submetric,
                        from_date=from_date,
                        ttl=cache_ttl,
                    ) or []
                except Exception:
                    return []
                vals = [float(v) for v in series if v is not None]
                if cache_dict is not None:
                    cache_dict[key] = {"ts": now, "vals": vals}
                return vals

            legacy.amboss_seed_series_7d = _amboss_seed_series_7d  # type: ignore
            legacy.amboss_series_generic = _amboss_series_generic  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            legacy.main(dry_run=dry_run)

        legacy_output = buf.getvalue().strip()
        segments = []
        if prelude:
            segments.append("\n".join(prelude))
        if legacy_output:
            segments.append(legacy_output)
        combined_output = "\n\n".join(segments)
        text_for_symptoms = combined_output or legacy_output
        if text_for_symptoms:
            self._store_last_symptoms(text_for_symptoms)
        return combined_output

    def _apply_mode_presets(self, mode: str, legacy) -> None:
        presets = get_mode_presets(mode)
        autofee_preset = presets.get("autofee", {})
        if not autofee_preset:
            return
        for attr, value in autofee_preset.items():
            if not hasattr(legacy, attr):
                continue
            if isinstance(value, dict):
                setattr(legacy, attr, dict(value))
            else:
                setattr(legacy, attr, value)

    @staticmethod
    def _is_pubkey(identifier: str) -> bool:
        return len(identifier) == 66 and all(ch in string.hexdigits for ch in identifier)

    def _pubkey_exclusions(self, entries: Dict[str, str]) -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = []
        for identifier, note in entries.items():
            if self._is_pubkey(identifier):
                result.append((identifier, note))
        return result

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        value = identifier.strip()
        while value.endswith(","):
            value = value[:-1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip()
