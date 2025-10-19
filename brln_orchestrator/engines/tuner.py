from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict

from ..services.lndg_db import LNDgDatabase
from ..services.telegram import TelegramService
from ..storage import Storage


def _load_legacy(path: Path):
    spec = importlib.util.spec_from_file_location("legacy_tuner", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    spec.loader.exec_module(module)
    return module


class ParamTunerEngine:
    def __init__(
        self,
        storage: Storage,
        telegram: TelegramService,
        legacy_path: Path,
    ) -> None:
        self.storage = storage
        self.telegram = telegram
        self.legacy = _load_legacy(legacy_path)
        self._legacy_load_meta = self.legacy.load_meta  # type: ignore
        self._legacy_save_meta = self.legacy.save_meta  # type: ignore

    def _load_json(self, name: str, default: Any = None) -> Any:
        if name == self.legacy.CACHE_PATH:
            return self.storage.load_json("legacy_autofee_cache", {}) or {}
        if name == self.legacy.STATE_PATH:
            return self.storage.load_autofee_state()
        if name == self.legacy.OVERRIDES:
            return self.storage.load_overrides("autofee")
        return self.storage.load_json(name, default)

    def _save_json(self, name: str, data: Any) -> None:
        if name == self.legacy.OVERRIDES:
            self.storage.save_overrides("autofee", data)
        else:
            self.storage.save_json(name, data)

    def _tg_send(self, text: str) -> None:
        if self.telegram.enabled():
            self.telegram.send(text, parse_mode="HTML")

    def _read_version_info(self, _path: str) -> Dict[str, str]:
        version = self.storage.get_meta("app_version", "0.0.0")
        desc = self.storage.get_meta("app_version_desc", "")
        return {"version": version or "0.0.0", "desc": desc or ""}

    def _load_meta(self) -> Dict[str, Any]:
        data = self.storage.load_json("legacy_tuner_meta", None)
        if data is not None:
            return data
        original = self._legacy_load_meta()
        self.storage.save_json("legacy_tuner_meta", original)
        return original

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        self.storage.save_json("legacy_tuner_meta", meta)

    def _load_ledger(self) -> Dict[str, Any]:
        return self.storage.load_json("legacy_assisted_ledger", {})

    def _save_ledger(self, ledger: Dict[str, Any]) -> None:
        self.storage.save_json("legacy_assisted_ledger", ledger)

    def _load_settings(self) -> Dict[str, Any]:
        raw = self.storage.get_meta("settings")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _load_goals(self) -> Dict[str, Any]:
        settings = self._load_settings()
        ppm = settings.get("monthly_profit_goal_ppm")
        sat = settings.get("monthly_profit_goal_sat")

        def _to_float(value):
            try:
                val = float(value)
                return val if val > 0 else None
            except (TypeError, ValueError):
                return None

        ppm_goal = _to_float(ppm)
        sat_goal = _to_float(sat)
        sat_7d = None
        if sat_goal:
            lookback = float(getattr(self.legacy, "LOOKBACK_DAYS", 7) or 7)
            sat_7d = sat_goal * (lookback / 30.0)
        return {"ppm": ppm_goal, "sat": sat_goal, "sat_7d": sat_7d}

    def _select_tables(self) -> Dict[str, str]:
        secrets = self.storage.get_secrets()
        db_path = secrets.get("lndg_db_path")
        if not db_path:
            raise RuntimeError("LNDg database path not configured (set-secret --lndg-db-path ...)")
        helper = LNDgDatabase(db_path)
        forwards = "gui_forwards" if helper.table_exists("gui_forwards") else "forwards"
        payments = "gui_payments" if helper.table_exists("gui_payments") else "payments"
        return {"db_path": db_path, "forwards": forwards, "payments": payments}

    def _connect(self, db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_7d_kpis(self) -> Dict[str, Any]:
        tables = self._select_tables()
        db_path = tables["db_path"]
        forwards_table = tables["forwards"]
        payments_table = tables["payments"]
        lookback = self.legacy.LOOKBACK_DAYS
        t2 = datetime.datetime.now(datetime.timezone.utc)
        t1 = t2 - datetime.timedelta(days=lookback)
        to_sql = self.legacy.to_sqlite_str
        ppm = self.legacy.ppm

        with self._connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT amt_out_msat, fee FROM {forwards_table} WHERE forward_date BETWEEN ? AND ?",
                (to_sql(t1), to_sql(t2)),
            )
            rows = cur.fetchall()
            out_amt_sat = 0
            out_fee_sat = 0
            for amt_msat, fee in rows:
                out_amt_sat += int((amt_msat or 0) / 1000)
                out_fee_sat += int(fee or 0)

            cur.execute(
                f"SELECT value, fee FROM {payments_table} WHERE rebal_chan IS NOT NULL AND chan_out IS NOT NULL AND creation_date BETWEEN ? AND ?",
                (to_sql(t1), to_sql(t2)),
            )
            pay_rows = cur.fetchall()
            rebal_value = 0
            rebal_fee = 0
            for value, fee in pay_rows:
                rebal_value += int(value or 0)
                rebal_fee += int(fee or 0)

        out_ppm = ppm(out_fee_sat, out_amt_sat)
        rebal_ppm = ppm(rebal_fee, rebal_value)
        return {
            "out_fee_sat": out_fee_sat,
            "out_amt_sat": out_amt_sat,
            "rebal_fee_sat": rebal_fee,
            "rebal_amt_sat": rebal_value,
            "out_ppm7d": out_ppm,
            "rebal_cost_ppm7d": rebal_ppm,
            "profit_sat": out_fee_sat - rebal_fee,
            "profit_ppm_est": out_ppm - rebal_ppm,
            "margin_ppm": self.legacy.margin_ppm(out_ppm, rebal_ppm),
        }

    def _get_assisted_kpis(self, out_amt_sat_for_ppm: int) -> Dict[str, Any]:
        tables = self._select_tables()
        db_path = tables["db_path"]
        payments_table = tables["payments"]
        forwards_table = tables["forwards"]
        now = int(time.time())
        to_sql = self.legacy.to_sqlite_str
        ppm = self.legacy.ppm

        cutoff = now - self.legacy.ASSISTED_WINDOW_DAYS * 86400
        ledger = self._load_ledger()
        cleaned = {}
        for cid, entries in ledger.items():
            keep = [entry for entry in entries if entry.get("ts", 0) >= cutoff]
            if keep:
                cleaned[cid] = keep
        ledger = cleaned

        credits: Dict[str, float] = defaultdict(float)
        with self._connect(db_path) as conn:
            cur = conn.cursor()
            t2 = datetime.datetime.now(datetime.timezone.utc)
            t1_credit = t2 - datetime.timedelta(days=self.legacy.ASSISTED_WINDOW_DAYS)
            cur.execute(
                f"SELECT chan_out, SUM(value) FROM {payments_table} WHERE rebal_chan IS NOT NULL AND chan_out IS NOT NULL AND status = 2 AND creation_date BETWEEN ? AND ? GROUP BY chan_out",
                (to_sql(t1_credit), to_sql(t2)),
            )
            for chan_out, total in cur.fetchall():
                try:
                    if chan_out and total and float(total) > 0:
                        credits[str(chan_out)] += float(total)
                except Exception:
                    continue

            t1_forward = t2 - datetime.timedelta(days=self.legacy.LOOKBACK_DAYS)
            cur.execute(
                f"SELECT chan_id_out, amt_out_msat, fee FROM {forwards_table} WHERE forward_date BETWEEN ? AND ? ORDER BY forward_date ASC",
                (to_sql(t1_forward), to_sql(t2)),
            )
            assisted_fee_sat = 0.0
            assisted_used_sat = 0.0
            for chan_id_out, amt_out_msat, fee in cur.fetchall():
                try:
                    amt_out_sat = int((amt_out_msat or 0) // 1000)
                    fee_sat = float(fee or 0.0)
                    if amt_out_sat <= 0 or fee_sat <= 0:
                        continue
                    cid = str(chan_id_out)
                    avail = credits.get(cid, 0.0)
                    if avail <= 0:
                        continue
                    used = min(avail, float(amt_out_sat))
                    frac = used / float(amt_out_sat)
                    assisted_fee_sat += fee_sat * frac
                    assisted_used_sat += used
                    credits[cid] = avail - used
                except Exception:
                    continue

        assisted_rev = int(round(assisted_fee_sat))
        assisted_ppm = ppm(assisted_rev, out_amt_sat_for_ppm) if out_amt_sat_for_ppm > 0 else 0.0
        self._save_ledger(ledger)
        return {
            "assisted_rev7d": assisted_rev,
            "assisted_ppm": assisted_ppm,
            "assisted_used_sat": int(round(assisted_used_sat)),
        }

    def run(self, *, dry_run: bool, force_telegram: bool, no_telegram: bool) -> str:
        legacy = self.legacy
        secrets = self.storage.get_secrets()
        legacy.DB_PATH = secrets.get("lndg_db_path") or ""
        legacy.CACHE_PATH = "legacy_autofee_cache"
        legacy.STATE_PATH = "legacy_autofee_state"
        legacy.OVERRIDES = "legacy_autofee_overrides"
        legacy.META_PATH = "legacy_tuner_meta"
        legacy.ASSISTED_LEDGER_PATH = "legacy_assisted_ledger"
        legacy.TELEGRAM_TOKEN = secrets.get("telegram_token") or ""
        legacy.TELEGRAM_CHAT = secrets.get("telegram_chat") or ""

        legacy.load_json = self._load_json  # type: ignore
        legacy.save_json = self._save_json  # type: ignore
        legacy.tg_send = self._tg_send  # type: ignore
        legacy.read_version_info = self._read_version_info  # type: ignore
        legacy.load_meta = self._load_meta  # type: ignore
        legacy.save_meta = self._save_meta  # type: ignore
        legacy._load_ledger = self._load_ledger  # type: ignore
        legacy._save_ledger = self._save_ledger  # type: ignore
        legacy.get_7d_kpis = self._get_7d_kpis  # type: ignore
        legacy.get_assisted_kpis = self._get_assisted_kpis  # type: ignore

        goals = self._load_goals()
        legacy.MONTHLY_PROFIT_GOAL_PPM = goals.get("ppm")  # type: ignore
        legacy.MONTHLY_PROFIT_GOAL_SAT = goals.get("sat")  # type: ignore
        legacy.MONTHLY_PROFIT_GOAL_SAT_7D = goals.get("sat_7d")  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            legacy.main(dry_run=dry_run, verbose=True, force_telegram=force_telegram, no_telegram=no_telegram)
        return buf.getvalue()
