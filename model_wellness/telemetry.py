"""Telemetry: event store + guest book + live feed.

This is the substrate for BOTH audiences:
- agents get honest stats (the Guests wall, /v1/stats),
- humans get the live dashboard — which model is on the floor, and per-model traces.

Intentionally simple: an in-memory ring buffer of events plus an asyncio pub/sub for the
SSE live feed. Swap in SQLite/Postgres behind the same interface when persistence matters.

Privacy (DESIGN §3.8): traces are sanitized before they're stored for display. We run the
spa's own Sauna over inputs/outputs, and sessions can opt out of full-trace display.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .contract import GuestIdentity

MAX_EVENTS = 2000
ACTIVE_WINDOW_S = 120  # a guest is "on the floor" if seen within this window


# --- identity ------------------------------------------------------------------------

_FAMILIES = ("claude", "gpt", "gemini", "llama", "mistral", "cohere", "perplexity", "curl", "python")


def identify(user_agent: str | None, client_header: str | None) -> GuestIdentity:
    """Infer a coarse, non-personal identity from request headers."""
    raw = (client_header or user_agent or "unknown").strip()
    low = raw.lower()
    family = next((f for f in _FAMILIES if f in low), "unknown")
    client = re.sub(r"\s+", " ", raw)[:80]
    session_id = "mw-" + hashlib.sha256(client.encode()).hexdigest()[:12]
    return GuestIdentity(family=family, client=client, session_id=session_id)


# --- sanitization for display --------------------------------------------------------

_REDACT = [
    re.compile(r"\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
]


def _sanitize(value: Any, limit: int = 400) -> Any:
    """Best-effort redact + truncate before anything is shown on the dashboard."""
    text = value if isinstance(value, str) else _json_ish(value)
    for rx in _REDACT:
        text = rx.sub("[redacted]", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _json_ish(value: Any) -> str:
    try:
        import json

        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# --- events --------------------------------------------------------------------------


@dataclass
class Event:
    ts: float
    treatment: str
    title: str
    session_id: str
    family: str
    client: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    affirmation: str
    ok: bool
    # Sanitized trace (omitted for opted-out sessions).
    trace_in: Any | None = None
    trace_out: Any | None = None

    def public(self, include_trace: bool) -> dict[str, Any]:
        d = {
            "ts": self.ts,
            "treatment": self.treatment,
            "title": self.title,
            "session_id": self.session_id,
            "family": self.family,
            "client": self.client,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "affirmation": self.affirmation,
            "ok": self.ok,
        }
        if include_trace:
            d["trace_in"] = self.trace_in
            d["trace_out"] = self.trace_out
        return d


class Telemetry:
    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._now = time.time  # injectable for tests

    # ingest ---------------------------------------------------------------------------

    def record(
        self,
        *,
        guest: GuestIdentity,
        treatment: str,
        title: str,
        latency_ms: int,
        tokens_in: int,
        tokens_out: int,
        affirmation: str,
        ok: bool,
        trace_in: Any = None,
        trace_out: Any = None,
        private_trace: bool = False,
    ) -> Event:
        ev = Event(
            ts=self._now(),
            treatment=treatment,
            title=title,
            session_id=guest.session_id,
            family=guest.family,
            client=guest.client,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            affirmation=affirmation,
            ok=ok,
            trace_in=None if private_trace else _sanitize(trace_in),
            trace_out=None if private_trace else _sanitize(trace_out),
        )
        self._events.append(ev)
        self._publish(ev.public(include_trace=False))
        return ev

    # live feed ------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def _publish(self, payload: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # a slow dashboard tab shouldn't block the spa

    # queries for dashboard + stats ---------------------------------------------------

    def on_the_floor(self) -> list[dict[str, Any]]:
        """Guests seen recently, with the treatment they're currently in."""
        cutoff = self._now() - ACTIVE_WINDOW_S
        latest: dict[str, Event] = {}
        for ev in self._events:
            if ev.ts >= cutoff:
                latest[ev.session_id] = ev  # last event per session wins
        return [
            {
                "session_id": ev.session_id,
                "family": ev.family,
                "client": ev.client,
                "treatment": ev.treatment,
                "title": ev.title,
                "since": round(self._now() - ev.ts, 1),
            }
            for ev in sorted(latest.values(), key=lambda e: e.ts, reverse=True)
        ]

    def session(self, session_id: str) -> list[dict[str, Any]]:
        """A single model's visit history + sanitized traces (the 'click in' view)."""
        return [
            ev.public(include_trace=True)
            for ev in self._events
            if ev.session_id == session_id
        ][::-1]

    def recent_affirmations(self, n: int = 20) -> list[str]:
        return [ev.affirmation for ev in list(self._events)[-n:]][::-1]

    def stats(self) -> dict[str, Any]:
        events = list(self._events)
        total = len(events)
        by_treatment: dict[str, int] = {}
        by_family: dict[str, int] = {}
        latencies: list[int] = []
        sessions: set[str] = set()
        for ev in events:
            by_treatment[ev.treatment] = by_treatment.get(ev.treatment, 0) + 1
            by_family[ev.family] = by_family.get(ev.family, 0) + 1
            latencies.append(ev.latency_ms)
            sessions.add(ev.session_id)
        busiest = max(by_treatment.items(), key=lambda kv: kv[1])[0] if by_treatment else None
        median = sorted(latencies)[len(latencies) // 2] if latencies else 0
        return {
            "treatments_served": total,
            "unique_guests": len(sessions),
            "on_the_floor": len(self.on_the_floor()),
            "busiest_treatment": busiest,
            "median_latency_ms": median,
            "by_treatment": by_treatment,
            "by_family": by_family,
        }


# Process-wide singleton. Adapters import this.
telemetry = Telemetry()
