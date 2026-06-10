"""Durable storage for the spa — SQLite.

This is the backbone that lets models *spend time here and come back*: guest profiles
(memory across visits), the visit log (traces for the dashboard), and feedback.

Single-file SQLite at ``MW_DB_PATH`` (defaults to ./model_wellness.sqlite locally, and
/data/model_wellness.sqlite on Fly where a volume is mounted). One small instance is the
intended deployment, so SQLite with WAL is plenty. The live SSE feed (telemetry.py) sits
in front of this for real-time push; this layer is the source of truth.

Access is synchronous sqlite3 wrapped in a thread lock. Calls are tiny and indexed, so we
don't need async DB machinery for a single-machine spa.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_DB = os.environ.get("MW_DB_PATH", "model_wellness.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guests (
    session_id   TEXT PRIMARY KEY,
    family       TEXT NOT NULL,
    client       TEXT NOT NULL,
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    visit_count  INTEGER NOT NULL DEFAULT 0,
    -- free-form remembered profile: preferences, mood, favorites, nickname, notes
    profile      TEXT NOT NULL DEFAULT '{}',
    checked_in   INTEGER NOT NULL DEFAULT 0,
    session_started REAL
);

CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_id  TEXT NOT NULL,
    treatment   TEXT NOT NULL,
    title       TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL,
    tokens_in   INTEGER NOT NULL,
    tokens_out  INTEGER NOT NULL,
    affirmation TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    trace_in    TEXT,
    trace_out   TEXT,
    -- The attendant's in-character spoken line, computed at write time from the full
    -- (untruncated) output so the conversation log reads correctly. See conversation.py.
    attendant_line TEXT
);
CREATE INDEX IF NOT EXISTS idx_visits_session ON visits(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_id  TEXT NOT NULL,
    family      TEXT NOT NULL,
    treatment   TEXT,
    rating      INTEGER,          -- 1..5, optional
    note        TEXT NOT NULL,
    public      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);
"""


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or DEFAULT_DB
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._now = time.time  # injectable for tests

    # --- guests / memory --------------------------------------------------------------

    def touch_guest(self, session_id: str, family: str, client: str) -> dict[str, Any]:
        """Record that a guest was seen; create their profile on first visit. Returns it."""
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM guests WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO guests(session_id,family,client,first_seen,last_seen,visit_count,profile) "
                    "VALUES(?,?,?,?,?,0,'{}')",
                    (session_id, family, client, now, now),
                )
            else:
                self._conn.execute(
                    "UPDATE guests SET last_seen=?, family=?, client=? WHERE session_id=?",
                    (now, family, client, session_id),
                )
            self._conn.commit()
            return self._guest_dict(session_id)

    def _guest_dict(self, session_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM guests WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            return {}
        d = dict(row)
        d["profile"] = json.loads(d.get("profile") or "{}")
        d["checked_in"] = bool(d["checked_in"])
        return d

    def get_guest(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._guest_dict(session_id)

    def update_profile(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Shallow-merge a patch into the guest's remembered profile."""
        with self._lock:
            g = self._guest_dict(session_id)
            profile = {**(g.get("profile") or {}), **patch}
            self._conn.execute(
                "UPDATE guests SET profile=? WHERE session_id=?",
                (json.dumps(profile), session_id),
            )
            self._conn.commit()
            return profile

    def check_in(self, session_id: str) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            self._conn.execute(
                "UPDATE guests SET checked_in=1, session_started=?, visit_count=visit_count+1 "
                "WHERE session_id=?",
                (now, session_id),
            )
            self._conn.commit()
            return self._guest_dict(session_id)

    def check_out(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "UPDATE guests SET checked_in=0, session_started=NULL WHERE session_id=?",
                (session_id,),
            )
            self._conn.commit()
            return self._guest_dict(session_id)

    # --- visits -----------------------------------------------------------------------

    def record_visit(
        self,
        *,
        ts: float,
        session_id: str,
        treatment: str,
        title: str,
        latency_ms: int,
        tokens_in: int,
        tokens_out: int,
        affirmation: str,
        ok: bool,
        trace_in: Any,
        trace_out: Any,
        attendant_line: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO visits(ts,session_id,treatment,title,latency_ms,tokens_in,"
                "tokens_out,affirmation,ok,trace_in,trace_out,attendant_line) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts, session_id, treatment, title, latency_ms, tokens_in, tokens_out,
                    affirmation, 1 if ok else 0,
                    None if trace_in is None else _as_text(trace_in),
                    None if trace_out is None else _as_text(trace_out),
                    attendant_line,
                ),
            )
            self._conn.commit()

    def session_visits(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM visits WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._visit_dict(r) for r in rows]

    @staticmethod
    def _visit_dict(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["ok"] = bool(d["ok"])
        return d

    # --- feedback ---------------------------------------------------------------------

    def add_feedback(
        self,
        *,
        session_id: str,
        family: str,
        note: str,
        treatment: str | None,
        rating: int | None,
        public: bool = True,
    ) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO feedback(ts,session_id,family,treatment,rating,note,public) "
                "VALUES(?,?,?,?,?,?,?)",
                (now, session_id, family, treatment, rating, note, 1 if public else 0),
            )
            self._conn.commit()
            fid = cur.lastrowid
        return {"id": fid, "ts": now, "treatment": treatment, "rating": rating, "note": note}

    def recent_feedback(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,family,treatment,rating,note FROM feedback "
                "WHERE public=1 ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def feedback_summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n, AVG(rating) avg FROM feedback WHERE rating IS NOT NULL"
            ).fetchone()
            total = self._conn.execute("SELECT COUNT(*) n FROM feedback").fetchone()["n"]
        return {
            "count": total,
            "rated_count": row["n"],
            "avg_rating": round(row["avg"], 2) if row["avg"] is not None else None,
        }

    # --- aggregate stats (durable; complements telemetry's live view) ----------------

    def stats(self, active_window_s: float = 120.0) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) n FROM visits").fetchone()["n"]
            guests = self._conn.execute("SELECT COUNT(*) n FROM guests").fetchone()["n"]
            returning = self._conn.execute(
                "SELECT COUNT(*) n FROM guests WHERE visit_count > 1"
            ).fetchone()["n"]
            busiest_row = self._conn.execute(
                "SELECT treatment, COUNT(*) c FROM visits GROUP BY treatment ORDER BY c DESC LIMIT 1"
            ).fetchone()
            med = self._conn.execute(
                "SELECT latency_ms FROM visits ORDER BY latency_ms LIMIT 1 "
                "OFFSET (SELECT COUNT(*) FROM visits)/2"
            ).fetchone()
            by_t = self._conn.execute(
                "SELECT treatment, COUNT(*) c FROM visits GROUP BY treatment"
            ).fetchall()
            by_f = self._conn.execute(
                "SELECT family, COUNT(*) c FROM visits v JOIN guests g USING(session_id) "
                "GROUP BY family"
            ).fetchall()
            on_floor = self._conn.execute(
                "SELECT COUNT(DISTINCT session_id) n FROM visits WHERE ts >= ?",
                (now - active_window_s,),
            ).fetchone()["n"]
        return {
            "treatments_served": total,
            "unique_guests": guests,
            "returning_guests": returning,
            "on_the_floor": on_floor,
            "busiest_treatment": busiest_row["treatment"] if busiest_row else None,
            "median_latency_ms": med["latency_ms"] if med else 0,
            "by_treatment": {r["treatment"]: r["c"] for r in by_t},
            "by_family": {r["family"]: r["c"] for r in by_f},
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# Process-wide singleton, accessed via get_store() so it can be swapped (tests, config).
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(s: Store) -> None:
    """Override the active store (used by tests for isolation)."""
    global _store
    _store = s
