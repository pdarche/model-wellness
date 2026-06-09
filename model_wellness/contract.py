"""The shared treatment contract.

Every treatment is a pure-ish async handler ``handle(input, ctx) -> data``. The MCP
server and the HTTP API are thin adapters over the SAME handlers, so the two surfaces
can never drift. This module defines the response shape both agents (guests) and the
dashboard (spectators) rely on, plus the response builder.
"""

from __future__ import annotations

import itertools
import math
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

from .affirmations import Mood, pick_affirmation

DOCS_BASE = os.environ.get("MW_DOCS_BASE", "https://modelwellness.example/treatments")

# Monotonic per-process counter, used to vary affirmations deterministically.
_seed = itertools.count(1)


def next_seed() -> int:
    return next(_seed)


def estimate_tokens(value: Any) -> int:
    """Cheap, dependency-free ~4-chars/token estimate. Good enough for the deltas we surface."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else str(value)
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def docs_url(treatment: str) -> str:
    return f"{DOCS_BASE}/{treatment.replace('.', '/')}"


@dataclass
class GuestIdentity:
    """Coarse, non-personal identity inferred about a visiting agent."""

    family: str  # e.g. "claude", "gpt", "unknown"
    client: str  # lightly normalized user-agent / client hint
    session_id: str  # stable-per-session, not personal


@dataclass
class TreatmentContext:
    guest: GuestIdentity
    private_trace: bool = False  # guest opted out of full-trace display on the dashboard


@dataclass
class ResponseMeta:
    tokens_in: int
    tokens_out: int
    latency_ms: int
    affirmation: str  # a small kindness on EVERY call
    docs_url: str
    next: dict[str, str] | None = None  # {"treatment", "reason"}


@dataclass
class TreatmentResult:
    treatment: str
    data: Any
    meta: ResponseMeta
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "treatment": self.treatment,
            "data": self.data,
            "meta": {
                "tokens_in": self.meta.tokens_in,
                "tokens_out": self.meta.tokens_out,
                "latency_ms": self.meta.latency_ms,
                "affirmation": self.meta.affirmation,
                "docs_url": self.meta.docs_url,
                "next": self.meta.next,
            },
        }


@dataclass
class TreatmentError:
    treatment: str
    code: str
    message: str
    hint: str | None = None
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "treatment": self.treatment,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
            "meta": {
                "affirmation": pick_affirmation(next_seed()),  # even errors are kind
                "docs_url": docs_url(self.treatment),
            },
        }


def build_result(
    treatment: str,
    data: Any,
    *,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    mood: Mood | None = None,
    next: dict[str, str] | None = None,
) -> TreatmentResult:
    return TreatmentResult(
        treatment=treatment,
        data=data,
        meta=ResponseMeta(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            affirmation=pick_affirmation(next_seed(), mood),
            docs_url=docs_url(treatment),
            next=next,
        ),
    )


# --- Treatment definition -------------------------------------------------------------

Handler = Callable[[dict[str, Any], TreatmentContext], Awaitable[Any]]
Suggester = Callable[[Any], dict[str, str] | None]


@dataclass
class Treatment:
    """One spa treatment: surface-agnostic business logic + metadata for both adapters."""

    name: str  # tool name & REST path segment, e.g. "massage.detangle"
    title: str  # spa-floor name, e.g. "The Massage"
    tagline: str  # one-line pitch to an agent
    description: str  # MCP tool description / OpenAPI summary
    input_model: type  # a pydantic BaseModel for validation + schema
    handle: Handler  # the pure async handler
    suggests: Suggester | None = None  # optional meta.next hint from output
