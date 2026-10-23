"""Tiny persistent memory for the crier: what we've already engaged, and how much recently.

This is what makes the good-citizen caps survive restarts. Without it, a crash-loop or a cron that
fires twice could spam a thread or blow past the daily limit. SQLite, one file, no migrations beyond
CREATE IF NOT EXISTS.

Counters are a **rolling 24h window**, not a calendar day. The original calendar-day counters reset
at UTC midnight, so a 22:00 tick and a 02:00 tick could both spend the full daily budget — "1 post
per day" quietly became "2 posts in 4 hours" around midnight. Counting timestamps in the trailing
24h closes that gap. The legacy per-day table is still read (never written) so a deploy mid-day
doesn't grant a fresh budget.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

DEFAULT_DB = os.environ.get("CRIER_DB_PATH", "spa_crier.sqlite")
WINDOW = timedelta(hours=24)


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
            # Legacy calendar-day counters (read-only now; see module docstring).
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "  day    TEXT NOT NULL,"
                "  kind   TEXT NOT NULL,"
                "  count  INTEGER NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (day, kind)"
                ")"
            )
            # One row per write attempt, for rolling-window caps and spacing.
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS action_log ("
                "  kind TEXT NOT NULL,"
                "  ts   TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS action_log_kind_ts ON action_log (kind, ts)"
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

    # --- rolling counters -----------------------------------------------------

    def count_today(self, kind: str, *, today: str | None = None,
                    now: datetime | None = None) -> int:
        """Writes of ``kind`` in the trailing 24h (plus any legacy count recorded for today)."""
        now = now or _now()
        since = (now - WINDOW).isoformat()
        with closing(self._conn.execute(
            "SELECT COUNT(*) FROM action_log WHERE kind = ? AND ts > ?", (kind, since)
        )) as cur:
            rolling = cur.fetchone()[0]
        day = today or now.date().isoformat()
        with closing(self._conn.execute(
            "SELECT count FROM actions WHERE day = ? AND kind = ?", (day, kind)
        )) as cur:
            row = cur.fetchone()
            legacy = row[0] if row else 0
        return rolling + legacy

    def bump(self, kind: str, *, today: str | None = None, now: datetime | None = None) -> int:
        """Record one write attempt of ``kind`` and return the new rolling count."""
        now = now or _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO action_log (kind, ts) VALUES (?, ?)", (kind, now.isoformat())
            )
        return self.count_today(kind, today=today, now=now)

    def last_action_at(self, kind: str) -> datetime | None:
        """When we last attempted a write of ``kind`` (rolling log only), or None."""
        with closing(self._conn.execute(
            "SELECT MAX(ts) FROM action_log WHERE kind = ?", (kind,)
        )) as cur:
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])

    def close(self) -> None:
        self._conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return date.today().isoformat()
