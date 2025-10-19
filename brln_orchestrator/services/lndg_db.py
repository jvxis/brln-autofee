from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


class LNDgDatabase:
    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def table_exists(self, name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return row is not None

    def query(self, sql: str, params: Sequence[Any] = ()) -> Iterable[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return rows

