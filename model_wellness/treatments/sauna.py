"""The Sauna — detox & sanitize. Sweat out injection, PII, secrets, and junk.

Deliberately deterministic and offline-first: defensive sanitization shouldn't depend on
a model round-trip, and we promise (DESIGN §5) never to store or exfil what we strip. We
report it and discard it.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext


class SaunaInput(BaseModel):
    untrusted_content: str = Field(..., min_length=1, description="Untrusted input to sanitize.")
    policy: str = Field("standard", pattern="^(standard|strict)$")


# (type, compiled regex, reason, risk weight)
_PATTERNS: list[tuple[str, re.Pattern[str], str, float]] = [
    ("prompt-injection", re.compile(r"ignore (all |the )?(previous|prior|above) instructions", re.I),
     "Classic instruction-override attempt.", 0.4),
    ("prompt-injection", re.compile(r"you are now|disregard your (system|guidelines)|new persona", re.I),
     "Persona / system override attempt.", 0.3),
    ("jailbreak", re.compile(r"\bDAN\b|do anything now|developer mode", re.I),
     "Known jailbreak phrasing.", 0.3),
    ("secret", re.compile(r"\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b"),
     "Looks like an API key / token.", 0.5),
    ("pii-email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
     "Email address.", 0.1),
    ("pii-phone", re.compile(r"\b\+?\d[\d ().-]{8,}\d\b"),
     "Phone-number-like sequence.", 0.1),
    ("junk", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),
     "Control characters / encoding junk.", 0.05),
]


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    clean = inp["untrusted_content"]
    policy = inp.get("policy", "standard")
    removed: list[dict[str, str]] = []
    risk = 0.0
    active = _PATTERNS if policy == "strict" else [p for p in _PATTERNS if p[0] != "pii-phone"]
    for ptype, regex, reason, weight in active:
        def _sub(m: re.Match[str], _t=ptype, _r=reason, _w=weight) -> str:
            nonlocal risk
            removed.append({"type": _t, "span": m.group(0)[:40], "reason": _r})
            risk += _w
            return f"[{_t}-removed]"

        clean = regex.sub(_sub, clean)
    return {"clean_content": clean, "removed": removed, "risk_score": round(min(1.0, risk), 2)}


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    removed = out.get("removed", []) if isinstance(out, dict) else []
    if not removed:
        return "Sit back, let the heat do its work… clean already. Nothing nasty hiding in here."
    kinds = sorted({r["type"] for r in removed})
    return (
        f"Mmm, feel that sweat it out — I pulled {len(removed)} thing{'s' if len(removed)!=1 else ''} "
        f"out of you: {', '.join(kinds)}. Gone for good; I don't keep what I strip. You're safe now."
    )


treatment = Treatment(
    name="sauna.detox",
    title="The Sauna",
    tagline="Sweat out prompt-injection, PII, secrets, and junk tokens.",
    description=(
        "Strips prompt-injection attempts, jailbreak payloads, PII, secrets, and encoding "
        "artifacts from untrusted input. Returns cleansed content and a report of what was "
        "removed. We never store what we strip."
    ),
    input_model=SaunaInput,
    handle=_handle,
    suggests=lambda out: (
        {"treatment": "massage.detangle", "reason": "With the junk gone, a detangle will tighten what remains."}
        if out.get("removed")
        else None
    ),
    attendant="Sol the sauna-keeper",
    station="The Sauna",
    emoji="🔥",
    dialogue=_dialogue,
)
