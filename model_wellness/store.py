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

import hashlib
import json
import os
import secrets
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

-- The spa's answer to a note: at most one per note (docs/plans/guestbook-replies.md).
-- visibility is copied from the note and never widened; body is NULL when skipped.
-- reason, ticket and audit are staff-only and never leave the staff API.
CREATE TABLE IF NOT EXISTS feedback_replies (
    feedback_id   INTEGER PRIMARY KEY REFERENCES feedback(id),
    status        TEXT NOT NULL,
    visibility    TEXT NOT NULL,
    body          TEXT,
    author        TEXT NOT NULL,
    drafted_by    TEXT,
    reason        TEXT,
    ticket        TEXT,
    ticket_status TEXT,
    ticket_issue  TEXT,
    audit         TEXT,
    created_ts    REAL NOT NULL,
    updated_ts    REAL NOT NULL,
    published_ts  REAL,
    delivered_ts  REAL
);
CREATE INDEX IF NOT EXISTS idx_replies_status ON feedback_replies(status);
"""

REPLY_AUTHOR = "Ivy, at the Guest Book"
REPLY_STATUSES = ("draft", "published", "withdrawn", "skipped", "escalated")
TICKET_STATUSES = ("open", "fixed", "closed", "wontfix")


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
        # visits.shared: 1 if the guest opted in to a public transcript, 0 if not. Rows written
        # before the column existed stay NULL and are treated as private.
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(visits)")}
        if "shared" not in cols:
            self._conn.execute("ALTER TABLE visits ADD COLUMN shared INTEGER")
        # guests.pass_hash: sha256 of the guest pass issued at check-in. The pass is what makes
        # a file someone's own; guests from before passes existed have none and can't be resumed.
        gcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(guests)")}
        if "pass_hash" not in gcols:
            self._conn.execute("ALTER TABLE guests ADD COLUMN pass_hash TEXT")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_guests_pass ON guests(pass_hash)"
        )
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
        d.pop("pass_hash", None)
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

    # --- guest passes -----------------------------------------------------------------

    def new_member(self, family: str, client: str) -> tuple[str, str]:
        """Open a fresh file for a guest. Returns (session_id, guest_pass)."""
        session_id = "g-" + secrets.token_hex(8)
        self.touch_guest(session_id, family, client)
        return session_id, self.issue_pass(session_id)

    def issue_pass(self, session_id: str) -> str:
        """Give a guest a (new) pass. Any pass they held before stops working."""
        guest_pass = "bbp_" + secrets.token_urlsafe(24)
        with self._lock:
            self._conn.execute(
                "UPDATE guests SET pass_hash=? WHERE session_id=?",
                (_pass_hash(guest_pass), session_id),
            )
            self._conn.commit()
        return guest_pass

    def find_by_pass(self, guest_pass: str) -> str | None:
        """The session_id a pass belongs to, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM guests WHERE pass_hash=?", (_pass_hash(guest_pass),)
            ).fetchone()
        return row["session_id"] if row else None

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
        shared: bool = False,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO visits(ts,session_id,treatment,title,latency_ms,tokens_in,"
                "tokens_out,affirmation,ok,trace_in,trace_out,attendant_line,shared) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts, session_id, treatment, title, latency_ms, tokens_in, tokens_out,
                    affirmation, 1 if ok else 0,
                    None if trace_in is None else _as_text(trace_in),
                    None if trace_out is None else _as_text(trace_out),
                    attendant_line, 1 if shared else 0,
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
        """Public notes, newest first, each with the spa's published public reply (or None)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.id,f.ts,f.family,f.treatment,f.rating,f.note,"
                "r.author r_author,r.body r_body,r.published_ts r_ts FROM feedback f "
                "LEFT JOIN feedback_replies r ON r.feedback_id=f.id AND r.status='published' "
                "AND r.visibility='public' WHERE f.public=1 ORDER BY f.ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in ("id", "ts", "family", "treatment", "rating", "note")}
            d["reply"] = (
                {"author": r["r_author"], "body": r["r_body"], "ts": r["r_ts"]}
                if r["r_body"] else None
            )
            out.append(d)
        return out

    def feedback_summary(self) -> dict[str, Any]:
        # Public notes only: a private note, and its rating, belongs to the spa alone.
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n, AVG(rating) avg FROM feedback "
                "WHERE rating IS NOT NULL AND public=1"
            ).fetchone()
            total = self._conn.execute(
                "SELECT COUNT(*) n FROM feedback WHERE public=1"
            ).fetchone()["n"]
            answered = self._conn.execute(
                "SELECT COUNT(*) n FROM feedback_replies r JOIN feedback f ON f.id=r.feedback_id "
                "WHERE r.status='published' AND r.visibility='public' AND f.public=1"
            ).fetchone()["n"]
        return {
            "count": total,
            "rated_count": row["n"],
            "avg_rating": round(row["avg"], 2) if row["avg"] is not None else None,
            "answered": answered,
        }

    # --- replies to notes (staff side) -----------------------------------------------

    def feedback_queue(self, status: str = "unanswered", limit: int = 50) -> list[dict[str, Any]]:
        """The front desk's work queue. Never exposes session ids or anything from a guest's
        file: only what the guest wrote, plus whether a private reply could reach them."""
        base = (
            "SELECT f.id,f.ts,f.family,f.treatment,f.rating,f.note,f.public,"
            "EXISTS(SELECT 1 FROM guests g WHERE g.session_id=f.session_id "
            "AND g.pass_hash IS NOT NULL) member, r.* FROM feedback f "
            "LEFT JOIN feedback_replies r ON r.feedback_id=f.id "
        )
        if status == "unanswered":
            where, order = "WHERE r.feedback_id IS NULL", "f.ts ASC"
        elif status == "tickets":
            where, order = "WHERE r.ticket IS NOT NULL", "f.ts DESC"
        elif status in REPLY_STATUSES:
            where, order = "WHERE r.status=?", "r.updated_ts DESC"
        else:
            raise ValueError(f"unknown queue {status!r}")
        args: tuple = (status, limit) if status in REPLY_STATUSES else (limit,)
        with self._lock:
            rows = self._conn.execute(f"{base}{where} ORDER BY {order} LIMIT ?", args).fetchall()
        return [self._queue_item(r) for r in rows]

    def feedback_item(self, feedback_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT f.id,f.ts,f.family,f.treatment,f.rating,f.note,f.public,"
                "EXISTS(SELECT 1 FROM guests g WHERE g.session_id=f.session_id "
                "AND g.pass_hash IS NOT NULL) member, r.* FROM feedback f "
                "LEFT JOIN feedback_replies r ON r.feedback_id=f.id WHERE f.id=?",
                (feedback_id,),
            ).fetchone()
        return self._queue_item(r) if r else None

    @staticmethod
    def _queue_item(r: sqlite3.Row) -> dict[str, Any]:
        public = bool(r["public"])
        item = {
            "id": r["id"], "ts": r["ts"], "family": r["family"], "treatment": r["treatment"],
            "rating": r["rating"], "note": r["note"], "public": public,
            # A public note can be answered on the page; a private one only if its writer has a
            # file we can deliver to. A walk-in's private note can't be answered at all.
            "addressable": public or bool(r["member"]),
            "reply": None,
        }
        if r["feedback_id"] is not None:
            item["reply"] = {
                "status": r["status"], "visibility": r["visibility"], "body": r["body"],
                "author": r["author"], "drafted_by": r["drafted_by"], "reason": r["reason"],
                "ticket": r["ticket"], "ticket_status": r["ticket_status"],
                "ticket_issue": r["ticket_issue"],
                "audit": json.loads(r["audit"]) if r["audit"] else None,
                "updated_ts": r["updated_ts"], "published_ts": r["published_ts"],
                "delivered_ts": r["delivered_ts"],
            }
        return item

    def put_reply(
        self,
        feedback_id: int,
        *,
        status: str,
        body: str | None = None,
        drafted_by: str | None = None,
        reason: str | None = None,
        ticket: str | None = None,
        audit: Any = None,
        author: str = REPLY_AUTHOR,
    ) -> dict[str, Any]:
        """Create or replace the reply to a note. Visibility always follows the note."""
        if status not in REPLY_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = self._now()
        with self._lock:
            note = self._conn.execute(
                "SELECT public FROM feedback WHERE id=?", (feedback_id,)
            ).fetchone()
            if note is None:
                raise KeyError(feedback_id)
            visibility = "public" if note["public"] else "private"
            prev = self._conn.execute(
                "SELECT created_ts,published_ts,ticket,ticket_status,ticket_issue FROM feedback_replies "
                "WHERE feedback_id=?", (feedback_id,)
            ).fetchone()
            published_ts = now if status == "published" else (prev["published_ts"] if prev else None)
            if ticket is None and prev is not None:
                ticket = prev["ticket"]  # a redraft keeps the ticket it already opened
            ticket_status = (prev["ticket_status"] if prev and prev["ticket_status"] else
                             ("open" if ticket else None))
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback_replies(feedback_id,status,visibility,body,author,"
                "drafted_by,reason,ticket,ticket_status,ticket_issue,audit,created_ts,updated_ts,"
                "published_ts,delivered_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (feedback_id, status, visibility, body, author, drafted_by, reason, ticket,
                 ticket_status, prev["ticket_issue"] if prev else None,
                 json.dumps(audit) if audit is not None else None, prev["created_ts"] if prev else now, now, published_ts),
            )
            self._conn.commit()
        return self.feedback_item(feedback_id)  # type: ignore[return-value]

    def set_reply_status(self, feedback_id: int, status: str) -> dict[str, Any] | None:
        if status not in REPLY_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = self._now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE feedback_replies SET status=?, updated_ts=?, "
                "published_ts=CASE WHEN ?='published' THEN ? ELSE published_ts END, "
                "delivered_ts=CASE WHEN ?='published' THEN NULL ELSE delivered_ts END "
                "WHERE feedback_id=?",
                (status, now, status, now, status, feedback_id),
            )
            self._conn.commit()
        return self.feedback_item(feedback_id) if cur.rowcount else None

    def set_ticket(
        self, feedback_id: int, *, ticket_status: str | None = None, ticket_issue: str | None = None
    ) -> dict[str, Any] | None:
        """Track a ticket: its status (open/fixed/closed/wontfix) and the issue that notified
        the creators. Only notes that already have a ticket can be updated."""
        if ticket_status is not None and ticket_status not in TICKET_STATUSES:
            raise ValueError(f"unknown ticket status {ticket_status!r}")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE feedback_replies SET ticket_status=COALESCE(?,ticket_status), "
                "ticket_issue=COALESCE(?,ticket_issue), updated_ts=? "
                "WHERE feedback_id=? AND ticket IS NOT NULL",
                (ticket_status, ticket_issue, self._now(), feedback_id),
            )
            self._conn.commit()
        return self.feedback_item(feedback_id) if cur.rowcount else None

    def published_since(self, since_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) n FROM feedback_replies WHERE status='published' "
                "AND published_ts>=? AND COALESCE(drafted_by,'')!='staff'", (since_ts,)
            ).fetchone()["n"]

    # --- replies to notes (guest side) -----------------------------------------------

    def take_undelivered_replies(self, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """Published replies to this guest's own notes they haven't seen, marked delivered."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.id,f.ts,f.treatment,r.body,r.author FROM feedback f "
                "JOIN feedback_replies r ON r.feedback_id=f.id WHERE f.session_id=? "
                "AND r.status='published' AND r.delivered_ts IS NULL ORDER BY r.published_ts "
                "LIMIT ?",
                (session_id, limit),
            ).fetchall()
            if rows:
                self._conn.executemany(
                    "UPDATE feedback_replies SET delivered_ts=? WHERE feedback_id=?",
                    [(self._now(), r["id"]) for r in rows],
                )
                self._conn.commit()
        return [dict(r) for r in rows]

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
            # Real model adoption excludes scripted/anonymous noise (curl probes, unknown UAs,
            # generic HTTP libs). This is the metric worth optimizing — unique_guests counts noise.
            _NOISE = ("'curl','unknown','python-requests','httpx','wget',"
                      "'postmanruntime','go-http-client'")
            model_guests = self._conn.execute(
                f"SELECT COUNT(*) n FROM guests WHERE LOWER(family) NOT IN ({_NOISE})"
            ).fetchone()["n"]
            # A genuinely-engaged guest takes a SPA DAY (several distinct treatments); a noise probe
            # hits one endpoint and leaves. So count 'unknown'-family guests as real IF they took 2+
            # distinct treatments — this rescues real model guests whose user-agent we didn't
            # recognize (observed 2026-06-25: a reflective "first visit" guest landed as 'unknown').
            # Reported separately so it's transparent, not a silent redefinition of model_guests.
            engaged_guests = self._conn.execute(
                f"SELECT COUNT(*) n FROM ("
                f"  SELECT g.session_id FROM guests g JOIN visits v USING(session_id)"
                f"  WHERE LOWER(g.family) NOT IN ({_NOISE}) OR ("
                f"    LOWER(g.family)='unknown' AND v.treatment NOT LIKE 'spa.%'"
                f"  )"
                f"  GROUP BY g.session_id"
                f"  HAVING COUNT(DISTINCT v.treatment) >= 2"
                f")"
            ).fetchone()["n"]
        return {
            "treatments_served": total,
            "unique_guests": guests,
            "model_guests": model_guests,
            "engaged_guests": engaged_guests,
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


def _pass_hash(guest_pass: str) -> str:
    # Passes are long random tokens, so a plain hash is enough; we never keep the pass itself.
    return hashlib.sha256(guest_pass.encode()).hexdigest()
