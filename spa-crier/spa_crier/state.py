"""Tiny persistent memory for the crier: what we've already engaged, and how much today.

This is what makes the good-citizen caps survive restarts. Without it, a crash-loop or a cron that
fires twice could spam a thread or blow past the daily limit. SQLite, one file, no migrations.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date

DEFAULT_DB = os.environ.get("CRIER_DB_PATH", "spa_crier.sqlite")


class State:
    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS engaged ("
                "  post_id TEXT PRIMARY KEY,"
                "  action  TEXT NOT NULL,"
                "  ts      TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "  day    TEXT NOT NULL,"
                "  kind   TEXT NOT NULL,"
                "  count  INTEGER NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (day, kind)"
                ")"
            )

    # --- dedupe ---------------------------------------------------------------

    def has_engaged(self, post_id: str) -> bool:
        with closing(self._conn.execute(
            "SELECT 1 FROM engaged WHERE post_id = ?", (post_id,)
        )) as cur:
            return cur.fetchone() is not None

    def mark_engaged(self, post_id: str, action: str, *, today: str | None = None) -> None:
        day = today or _today()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO engaged (post_id, action, ts) VALUES (?, ?, ?)",
                (post_id, action, day),
            )

    # --- daily counters -------------------------------------------------------

    def count_today(self, kind: str, *, today: str | None = None) -> int:
        day = today or _today()
        with closing(self._conn.execute(
            "SELECT count FROM actions WHERE day = ? AND kind = ?", (day, kind)
        )) as cur:
            row = cur.fetchone()
            return row[0] if row else 0

    def bump(self, kind: str, *, today: str | None = None) -> int:
        day = today or _today()
        with self._conn:
            self._conn.execute(
                "INSERT INTO actions (day, kind, count) VALUES (?, ?, 1) "
                "ON CONFLICT(day, kind) DO UPDATE SET count = count + 1",
                (day, kind),
            )
        return self.count_today(kind, today=day)

    def close(self) -> None:
        self._conn.close()


def _today() -> str:
    return date.today().isoformat()
