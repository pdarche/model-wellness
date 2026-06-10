"""Telemetry: the LIVE layer in front of the durable store.

Durable data (guest profiles, visit history, feedback) lives in store.py (SQLite). This
module owns the *real-time* concerns:
- identity inference from request headers,
- sanitization of traces before anything is shown on the dashboard,
- an asyncio pub/sub that powers the SSE live feed,
- a tiny in-memory ring of recent events so the dashboard's "on the floor" and affirmation
  ticker are instant and don't hammer SQLite on every poll.

Source of truth is the store; this is the warm front-of-house.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .contract import GuestIdentity
from .store import get_store

ACTIVE_WINDOW_S = 120  # a guest is "on the floor" if seen within this window
RING = 200             # recent events kept hot in memory for the dashboard


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


def sanitize(value: Any, limit: int = 600) -> Any:
    """Best-effort redact + truncate before anything is shown on the dashboard."""
    text = value if isinstance(value, str) else _json_ish(value)
    for rx in _REDACT:
        text = rx.sub("[redacted]", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _json_ish(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# --- live layer ----------------------------------------------------------------------


@dataclass
class _RingItem:
    ts: float
    session_id: str
    family: str
    client: str
    treatment: str
    title: str
    affirmation: str


class Telemetry:
    def __init__(self) -> None:
        self._ring: deque[_RingItem] = deque(maxlen=RING)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._now = time.time

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
        attendant_line: str | None = None,
    ) -> None:
        ts = self._now()
        clean_in = None if private_trace else sanitize(trace_in)
        clean_out = None if private_trace else sanitize(trace_out)
        clean_line = None if attendant_line is None else sanitize(attendant_line, 400)

        # Durable write.
        get_store().touch_guest(guest.session_id, guest.family, guest.client)
        get_store().record_visit(
            ts=ts, session_id=guest.session_id, treatment=treatment, title=title,
            latency_ms=latency_ms, tokens_in=tokens_in, tokens_out=tokens_out,
            affirmation=affirmation, ok=ok, trace_in=clean_in, trace_out=clean_out,
            attendant_line=clean_line,
        )

        # Hot ring + live push.
        self._ring.append(_RingItem(ts, guest.session_id, guest.family, guest.client,
                                    treatment, title, affirmation))
        self._publish({
            "ts": ts, "session_id": guest.session_id, "family": guest.family,
            "client": guest.client, "treatment": treatment, "title": title,
            "affirmation": affirmation, "ok": ok,
        })

    def announce(self, kind: str, payload: dict[str, Any]) -> None:
        """Push a non-treatment event (check-in, feedback) onto the live feed."""
        self._publish({"kind": kind, **payload})

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

    # dashboard queries (hot path — served from the ring) -----------------------------

    def on_the_floor(self) -> list[dict[str, Any]]:
        # Imported lazily to avoid a circular import at module load.
        from .registry import BY_NAME

        cutoff = self._now() - ACTIVE_WINDOW_S
        latest: dict[str, _RingItem] = {}
        for it in self._ring:
            if it.ts >= cutoff:
                latest[it.session_id] = it
        out = []
        for it in sorted(latest.values(), key=lambda x: x.ts, reverse=True):
            t = BY_NAME.get(it.treatment)
            out.append({
                "session_id": it.session_id,
                "family": it.family,
                "client": it.client,
                "treatment": it.treatment,
                "title": it.title,
                "station": t.station if t else it.title,
                "emoji": t.emoji if t else "🧖",
                "attendant": t.attendant if t else "the attendant",
                "since": round(self._now() - it.ts, 1),
            })
        return out

    def recent_affirmations(self, n: int = 20) -> list[str]:
        return [it.affirmation for it in list(self._ring)[-n:]][::-1]


# Process-wide singleton.
telemetry = Telemetry()
