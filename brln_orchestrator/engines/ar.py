from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..presets import get_mode_presets
from ..services.lndg_api import LNDgAPI
from ..services.lndg_db import LNDgDatabase
from ..services.telegram import TelegramService
from ..storage import Storage


def _load_legacy(path: Path):
    spec = importlib.util.spec_from_file_location("legacy_ar", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    spec.loader.exec_module(module)
    return module


class ARTriggerEngine:
    def __init__(
        self,
        storage: Storage,
        lndg_api: LNDgAPI,
        telegram: TelegramService,
        legacy_path: Path,
    ) -> None:
        self.storage = storage
        self.lndg_api = lndg_api
        self.telegram = telegram
        self.legacy = _load_legacy(legacy_path)
        self._legacy_load_autofee_params = None
        self._autofee_param_overrides: Dict[str, float] = {}
        self._dry_run = False
        self._pending_updates: list[tuple[str, Dict[str, Any]]] = []
        self._captured_messages: list[str] = []

    def _load_json(self, name: str) -> Dict[str, Any]:
        if name == self.legacy.CACHE_PATH:
            return self.storage.load_json("legacy_autofee_cache", {})
        if name == self.legacy.STATE_PATH:
            # AR Trigger expects same state as AutoFee
            return self.storage.load_autofee_state()
        return self.storage.load_json(name, {})

    def _save_json(self, name: str, data: Dict[str, Any]) -> None:
        if name == self.legacy.STATE_PATH:
            self.storage.save_autofee_state(data)
        else:
            self.storage.save_json(name, data)

    async def _tg_send(self, session: Any, text: str) -> None:  # session unused in new service
        self._captured_messages.append(text)
        if self._dry_run:
            return
        if self.telegram.enabled():
            self.telegram.send(text)

    async def _fetch_all_channels(self, session: Any) -> list[Dict[str, Any]]:  # session unused
        return self.lndg_api.list_channels()

    async def _update_channel(self, session: Any, chan_id: str, payload: Dict[str, Any]) -> None:
        self._pending_updates.append((chan_id, payload))
        if self._dry_run:
            return
        self.lndg_api.update_channel(chan_id, payload)

    def _read_version_info(self, _path: str) -> Dict[str, str]:
        version = self.storage.get_meta("app_version", "0.0.0")
        desc = self.storage.get_meta("app_version_desc", "")
        return {"version": version or "0.0.0", "desc": desc or ""}

    def _load_autofee_params(self) -> Dict[str, Any]:
        # Fallback to legacy JSON store if present
        params = self.storage.load_json("legacy_autofee_params", None)
        if params is None:
            original = self._legacy_load_autofee_params
            params = original() if original else {}
            self.storage.save_json("legacy_autofee_params", params)
        if not isinstance(params, dict):
            params = {}
        overrides = self._autofee_param_overrides or {}
        if not overrides:
            return params
        merged = dict(params)
        merged.update(overrides)
        return merged

    def _patch_rebal_sql(self) -> None:
        secrets = self.storage.get_secrets()
        db_path = secrets.get("lndg_db_path")
        if not db_path:
            return
        helper = LNDgDatabase(db_path)
        if not helper.table_exists("gui_payments") and helper.table_exists("payments"):
            self.legacy.load_rebal_costs = _wrap_load_rebal_costs(self.legacy.load_rebal_costs, "payments")  # type: ignore

    def run(self, *, dry_run: bool, mode: str = "conservador", no_telegram_when_no_changes: bool = False) -> str:
        legacy = self.legacy

        self._dry_run = dry_run
        self._pending_updates = []
        self._captured_messages = []

        secrets = self.storage.get_secrets()
        self._apply_mode_presets(mode or "conservador", legacy)
        exclusions = self.storage.list_exclusions()
        channel_exclusions_map: Dict[str, str] = {}
        for identifier, note in exclusions.items():
            norm = self._normalize_identifier(identifier)
            if norm and norm.isdigit():
                channel_exclusions_map[norm] = note or ""
        channel_exclusions: List[Tuple[str, str]] = sorted(channel_exclusions_map.items())

        forced_raw = self.storage.list_forced_sources()
        forced_channels: List[Tuple[str, str]] = []
        for identifier, note in forced_raw.items():
            norm = self._normalize_identifier(identifier)
            if norm and norm.isdigit():
                forced_channels.append((norm, note or ""))
        forced_channels = sorted(forced_channels)

        legacy.DB_PATH = secrets.get("lndg_db_path") or ""
        legacy.TELEGRAM_TOKEN = secrets.get("telegram_token") or ""
        legacy.CHATID = secrets.get("telegram_chat") or ""
        legacy.SEND_TELEGRAM_WHEN_NO_CHANGES = not no_telegram_when_no_changes
        legacy.CACHE_PATH = "legacy_autofee_cache"
        legacy.STATE_PATH = "legacy_autofee_state"

        # Exclusions (channel IDs)
        legacy.EXCLUSION_LIST = [identifier for identifier, _ in channel_exclusions]
        legacy.FORCE_SOURCE_LIST = set(identifier for identifier, _ in forced_channels)

        exclusion_notes: List[str] = []
        if dry_run and channel_exclusions:
            exclusion_notes.append(f"[dry-run] AR Trigger ignorando {len(channel_exclusions)} channel id(s) por exclusao:")
            for identifier, note in sorted(channel_exclusions):
                suffix = f" ({note})" if note else ""
                exclusion_notes.append(f"  - {identifier}{suffix}")

        forced_notes: List[str] = []
        if dry_run and forced_channels:
            forced_notes.append(f"[dry-run] AR Trigger forçando {len(forced_channels)} channel id(s) como source:")
            for identifier, note in forced_channels:
                suffix = f" ({note})" if note else ""
                forced_notes.append(f"  - {identifier}{suffix}")

        legacy.load_json = self._load_json  # type: ignore
        legacy.save_json = self._save_json  # type: ignore
        legacy.tg_send = self._tg_send  # type: ignore
        legacy.fetch_all_channels = self._fetch_all_channels  # type: ignore
        legacy.update_channel = self._update_channel  # type: ignore
        legacy.read_version_info = self._read_version_info  # type: ignore
        self._legacy_load_autofee_params = legacy.load_autofee_params
        legacy.load_autofee_params = self._load_autofee_params  # type: ignore

        self._patch_rebal_sql()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            asyncio.run(legacy.main())  # legacy main already respects dry-run via state flags
        legacy_output = buf.getvalue().strip()

        updates = len(self._pending_updates)
        summary_lines = []
        if dry_run:
            summary_lines.append(f"[dry-run] ARTrigger processed {updates} pending update(s); nothing applied.")
            if self._captured_messages:
                header = self._captured_messages[-1].splitlines()[0]
                summary_lines.append(f"[dry-run] Telegram preview: {header}")
        else:
            summary_lines.append(f"ARTrigger applied {updates} update(s).")
            if self._captured_messages and not self.telegram.enabled():
                header = self._captured_messages[-1].splitlines()[0]
                summary_lines.append(f"Telegram disabled; preview: {header}")

        segments = []
        if exclusion_notes:
            segments.append("\n".join(exclusion_notes))
        if forced_notes:
            segments.append("\n".join(forced_notes))
        if legacy_output:
            segments.append(legacy_output)
        summary = "\n".join(summary_lines)
        if summary:
            segments.append(summary)
        return "\n\n".join(seg for seg in segments if seg)

    def _apply_mode_presets(self, mode: str, legacy) -> None:
        presets = get_mode_presets(mode)
        autofee_preset = presets.get("autofee", {})
        param_names = set(getattr(legacy, "AF_PARAM_NAMES", ()))
        if not param_names:
            param_names = {
                "LOW_OUTBOUND_THRESH",
                "HIGH_OUTBOUND_THRESH",
                "LOW_OUTBOUND_BUMP",
                "HIGH_OUTBOUND_CUT",
                "IDLE_EXTRA_CUT",
            }
        overrides: Dict[str, float] = {}
        for key, value in autofee_preset.items():
            if key not in param_names:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                overrides[key] = float(value)
        self._autofee_param_overrides = overrides

        ar_preset = presets.get("ar", {})
        for attr, value in ar_preset.items():
            if not hasattr(legacy, attr):
                continue
            if isinstance(value, dict):
                setattr(legacy, attr, dict(value))
            else:
                setattr(legacy, attr, value)

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        value = identifier.strip()
        while value.endswith(","):
            value = value[:-1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip()


def _wrap_load_rebal_costs(func, table_name: str):
    async def noop():  # pragma: no cover
        return None

    def wrapper(db_path: str, lookback_days: int = 7):
        try:
            return func(db_path, lookback_days)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            sql = func.__globals__["sql"].replace("FROM gui_payments", f"FROM {table_name}")
            func.__globals__["sql"] = sql
            return func(db_path, lookback_days)

    return wrapper

