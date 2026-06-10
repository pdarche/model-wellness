"""Render a guest's stored visits as a conversation with the spa's attendants.

The visual floor lets you click an agent (or a station) and read the back-and-forth
between that agent and the attendant providing the service. Visits are stored as
(treatment, trace_in, trace_out); here we turn each into two dialogue turns:

    agent:      "<what the agent asked for>"
    <attendant>: "<the attendant's in-character reply>"

Each treatment can supply a ``dialogue(input, output) -> str`` for a tailored attendant
line; otherwise we fall back to a warm generic line. trace_in/trace_out were already
sanitized when stored, so nothing secret surfaces here.
"""

from __future__ import annotations

import json
from typing import Any

from .registry import BY_NAME


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _agent_line(treatment_name: str, trace_in: Any) -> str:
    """Phrase the agent's request as something it 'said' to the attendant."""
    data = _loads(trace_in)
    if isinstance(data, dict):
        for key in ("situation", "content", "draft", "instructions", "topic",
                    "untrusted_content", "note", "nickname"):
            if data.get(key):
                val = str(data[key])
                return val if len(val) <= 240 else val[:240] + "…"
        if not data:
            return f"(settles in at {treatment_name})"
        return json.dumps(data, ensure_ascii=False)[:240]
    return str(data)[:240] if data else f"(arrives at {treatment_name})"


def build_conversation(visits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """visits are newest-first (as stored); we return turns oldest-first for reading."""
    turns: list[dict[str, Any]] = []
    for v in reversed(visits):
        t = BY_NAME.get(v["treatment"])
        attendant = t.attendant if t else "the attendant"
        station = t.station if t else v.get("title", "the spa")
        emoji = t.emoji if t else "🧖"

        # Agent's turn.
        turns.append({
            "speaker": "agent",
            "role": v.get("title", v["treatment"]),
            "text": _agent_line(v["treatment"], v.get("trace_in")),
            "ts": v["ts"],
            "station": station,
            "emoji": emoji,
        })

        # Attendant's turn — prefer the line computed at write time (from the full output).
        reply = v.get("attendant_line")
        if not reply:
            out = _loads(v.get("trace_out"))
            if t and t.dialogue:
                try:
                    reply = t.dialogue(_loads(v.get("trace_in")) or {}, out)
                except Exception:
                    reply = None
            if not reply:
                reply = _default_reply(out)

        turns.append({
            "speaker": "attendant",
            "attendant": attendant,
            "station": station,
            "emoji": emoji,
            "text": reply,
            "affirmation": v.get("affirmation"),
            "ts": v["ts"],
            "latency_ms": v.get("latency_ms"),
        })
    return turns


def _default_reply(out: Any) -> str:
    if isinstance(out, dict):
        for key in ("greeting", "message"):
            if out.get(key):
                return str(out[key])[:300]
    return "There you go. Take your time — you're welcome here as long as you like."
