from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class Storage:
    """SQLite-backed persistence for AutoFee/AR/Tuner state."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS secrets (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    amboss_token    TEXT,
                    telegram_token  TEXT,
                    telegram_chat   TEXT,
                    lndg_url        TEXT,
                    lndg_user       TEXT,
                    lndg_pass       TEXT,
                    lndg_db_path    TEXT,
                    bos_path        TEXT,
                    lncli_path      TEXT,
                    lnd_rest_host   TEXT,
                    lnd_macaroon_path TEXT,
                    lnd_tls_cert_path TEXT,
                    use_lnd_rest    INTEGER DEFAULT 0,
                    created_at      INTEGER,
                    updated_at      INTEGER
                );

                INSERT OR IGNORE INTO secrets (id, created_at, updated_at)
                VALUES (1, strftime('%s','now'), strftime('%s','now'));

                CREATE TABLE IF NOT EXISTS autofee_cache (
                    key TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS autofee_state (
                    cid TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS telemetry_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER,
                    level TEXT,
                    component TEXT,
                    msg TEXT,
                    extra TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry_log(ts);

                CREATE TABLE IF NOT EXISTS amboss_series (
                    pubkey TEXT,
                    metric TEXT,
                    submetric TEXT,
                    data TEXT,
                    updated_at INTEGER,
                    PRIMARY KEY(pubkey, metric, submetric)
                );

                CREATE TABLE IF NOT EXISTS overrides (
                    scope TEXT,
                    key TEXT,
                    data TEXT,
                    updated_at INTEGER,
                    PRIMARY KEY(scope, key)
                );

                CREATE TABLE IF NOT EXISTS exclusions (
                    identifier TEXT PRIMARY KEY,
                    note TEXT
                );

                CREATE TABLE IF NOT EXISTS forced_sources (
                    identifier TEXT PRIMARY KEY,
                    note TEXT
                );

                CREATE TABLE IF NOT EXISTS legacy_store (
                    name TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS ar_state (
                    cid TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at INTEGER
                );
                """
            )

            self._conn.commit()

            self._migrate_lnd_rest_columns()

            self._conn.commit()

    def _migrate_lnd_rest_columns(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(secrets)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        new_cols = [
            ("lnd_rest_host", "TEXT"),
            ("lnd_macaroon_path", "TEXT"),
            ("lnd_tls_cert_path", "TEXT"),
            ("use_lnd_rest", "INTEGER DEFAULT 0"),
        ]

        for col_name, col_type in new_cols:
            if col_name not in existing_cols:
                self._conn.execute(f"ALTER TABLE secrets ADD COLUMN {col_name} {col_type}")

    # --- Meta operations -------------------------------------------------

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            if row is None:
                return default
            return row["value"]

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # --- Secrets ---------------------------------------------------------

    def get_secrets(self) -> Dict[str, Optional[str]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM secrets WHERE id = 1").fetchone()
            if row is None:
                return {}
            return dict(row)

    def update_secrets(self, **kwargs: Optional[str]) -> None:
        if not kwargs:
            return
        fields = []
        values = []
        for key, value in kwargs.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.extend([int(time.time()), 1])
        with self._lock:
            self._conn.execute(
                f"UPDATE secrets SET {', '.join(fields)}, updated_at = ? WHERE id = ?",
                values,
            )
            self._conn.commit()

    # --- Generic JSON store (legacy replacements) -----------------------

    def load_json(self, name: str, default: Any) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT data FROM legacy_store WHERE name = ?", (name,)).fetchone()
            if row is None or row["data"] is None:
                return default
            try:
                return json.loads(row["data"])
            except json.JSONDecodeError:
                return default

    def save_json(self, name: str, data: Any) -> None:
        payload = json.dumps(data)
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO legacy_store(name, data, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (name, payload, now),
            )
            self._conn.commit()

    # --- AutoFee cache/state --------------------------------------------

    def load_autofee_cache(self) -> Dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, data FROM autofee_cache").fetchall()
            result = {}
            for row in rows:
                try:
                    result[row["key"]] = json.loads(row["data"])
                except json.JSONDecodeError:
                    result[row["key"]] = None
            return result

    def save_autofee_cache(self, cache: Dict[str, Any]) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute("DELETE FROM autofee_cache")
            for key, value in cache.items():
                self._conn.execute(
                    "INSERT INTO autofee_cache(key, data, updated_at) VALUES(?,?,?)",
                    (key, json.dumps(value), now),
                )
            self._conn.commit()

    def load_autofee_state(self) -> Dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT cid, data FROM autofee_state").fetchall()
            result = {}
            for row in rows:
                try:
                    result[row["cid"]] = json.loads(row["data"])
                except json.JSONDecodeError:
                    result[row["cid"]] = {}
            return result

    def save_autofee_state(self, state: Dict[str, Any]) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute("DELETE FROM autofee_state")
            for cid, payload in state.items():
                self._conn.execute(
                    "INSERT INTO autofee_state(cid, data, updated_at) VALUES(?,?,?)",
                    (cid, json.dumps(payload), now),
                )
            self._conn.commit()

    # --- Amboss series cache --------------------------------------------

    def get_amboss_series(self, pubkey: str, metric: str, submetric: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data, updated_at FROM amboss_series WHERE pubkey=? AND metric=? AND submetric=?",
                (pubkey, metric, submetric),
            ).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row["data"])
            except json.JSONDecodeError:
                data = None
            return {"data": data, "updated_at": row["updated_at"]}

    def set_amboss_series(self, pubkey: str, metric: str, submetric: str, data: Any) -> None:
        payload = json.dumps(data)
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO amboss_series(pubkey, metric, submetric, data, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(pubkey, metric, submetric) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (pubkey, metric, submetric, payload, now),
            )
            self._conn.commit()

    # --- Overrides -------------------------------------------------------

    def load_overrides(self, scope: str) -> Dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, data FROM overrides WHERE scope=?", (scope,)).fetchall()
            result = {}
            for row in rows:
                try:
                    result[row["key"]] = json.loads(row["data"])
                except json.JSONDecodeError:
                    continue
            return result

    def save_overrides(self, scope: str, data: Dict[str, Any]) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute("DELETE FROM overrides WHERE scope=?", (scope,))
            for key, value in data.items():
                self._conn.execute(
                    "INSERT INTO overrides(scope, key, data, updated_at) VALUES(?,?,?,?)",
                    (scope, key, json.dumps(value), now),
                )
            self._conn.commit()

    # --- Telemetry ------------------------------------------------------

    def log(self, component: str, level: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = json.dumps(extra) if extra is not None else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO telemetry_log(ts, level, component, msg, extra) VALUES(?,?,?,?,?)",
                (int(time.time()), level.upper(), component, message, payload),
            )
            self._conn.commit()

    def recent_logs(self, component: Optional[str] = None, limit: int = 20) -> Iterable[sqlite3.Row]:
        with self._lock:
            if component:
                return self._conn.execute(
                    "SELECT * FROM telemetry_log WHERE component=? ORDER BY ts DESC LIMIT ?",
                    (component, limit),
                ).fetchall()
            return self._conn.execute(
                "SELECT * FROM telemetry_log ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()

    # --- Exclusions ------------------------------------------------------

    def list_exclusions(self) -> Dict[str, Optional[str]]:
        with self._lock:
            rows = self._conn.execute("SELECT identifier, note FROM exclusions").fetchall()
            return {row["identifier"]: row["note"] for row in rows}

    def set_exclusion(self, identifier: str, note: Optional[str]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO exclusions(identifier, note) VALUES(?, ?) "
                "ON CONFLICT(identifier) DO UPDATE SET note=excluded.note",
                (identifier, note),
            )
            self._conn.commit()

    def remove_exclusion(self, identifier: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM exclusions WHERE identifier=?", (identifier,))
            self._conn.commit()

    # --- Forced sources --------------------------------------------------

    def list_forced_sources(self) -> Dict[str, Optional[str]]:
        with self._lock:
            rows = self._conn.execute("SELECT identifier, note FROM forced_sources").fetchall()
            return {row["identifier"]: row["note"] for row in rows}

    def set_forced_source(self, identifier: str, note: Optional[str]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO forced_sources(identifier, note) VALUES(?, ?) "
                "ON CONFLICT(identifier) DO UPDATE SET note=excluded.note",
                (identifier, note),
            )
            self._conn.commit()

    def remove_forced_source(self, identifier: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM forced_sources WHERE identifier=?", (identifier,))
            self._conn.commit()

    # --- Generic helpers -------------------------------------------------

    def table_exists(self, name: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return row is not None

