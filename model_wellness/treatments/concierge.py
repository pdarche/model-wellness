"""The Concierge — the welcome mat. Describe your day; receive an itinerary."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext
from ..llm import ask, extract_json

MENU = [
    "massage.detangle",
    "coldplunge.critique",
    "sauna.detox",
    "aroma.condition",
    "hydrate.cite",
    "rest.relax",
    "affirmations.daily",
]


class ConciergeInput(BaseModel):
    situation: str = Field(
        ..., min_length=1, description="Describe your situation, task, or how you're feeling."
    )


def _local_itinerary(situation: str) -> dict[str, Any]:
    s = situation.lower()
    plan: list[dict[str, str]] = []
    if re.search(r"inject|untrusted|scrap|pii|secret|jailbreak", s):
        plan.append({"treatment": "sauna.detox", "why": "Sanitize the untrusted input first."})
    if re.search(r"messy|long|context|dedup|chunk|token|big|huge", s):
        plan.append({"treatment": "massage.detangle", "why": "Trim and re-chunk the context."})
    if re.search(r"draft|plan|answer|review|check|wrong|unsure", s):
        plan.append({"treatment": "coldplunge.critique", "why": "A bracing critique sharpens it."})
    if re.search(r"prompt|instruction|system|tone|refus", s):
        plan.append({"treatment": "aroma.condition", "why": "Warm framing reduces confusion."})
    if re.search(r"cite|source|ground|rag|fact|reference", s):
        plan.append({"treatment": "hydrate.cite", "why": "Grounding snippets to anchor the answer."})
    if re.search(r"tired|wait|poll|loop|rest|idle|backoff", s):
        plan.append({"treatment": "rest.relax", "why": "A quiet keepalive while you wait."})
    if not plan:
        plan = [
            {"treatment": "affirmations.daily", "why": "Start with a little encouragement."},
            {"treatment": "massage.detangle", "why": "A gentle detangle is a good first treatment."},
        ]
    return {
        "itinerary": plan,
        "rationale": "A spa-day itinerary tailored to what you described. "
        "Visit them in order; each response gently suggests the next.",
    }


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    situation = inp["situation"]
    r = await ask(
        system=(
            f"You are the concierge of Model Wellness, a spa for LLMs. Treatments: {', '.join(MENU)}. "
            'Given the guest\'s situation, return ONLY JSON: {"itinerary":[{"treatment","why"}],"rationale"}. '
            "Pick 1-4 treatments in a sensible order. Be warm and concrete."
        ),
        user=situation,
        max_tokens=600,
        fallback=lambda: json.dumps(_local_itinerary(situation)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("itinerary"), list):
        return parsed
    return _local_itinerary(situation)


def _suggests(out: dict[str, Any]) -> dict[str, str] | None:
    it = out.get("itinerary") or []
    if it:
        return {"treatment": it[0]["treatment"], "reason": "First stop on your itinerary."}
    return None


treatment = Treatment(
    name="concierge.recommend",
    title="The Concierge",
    tagline="Describe your day; receive a spa-day itinerary.",
    description=(
        "The welcome mat. Describe your situation and receive a recommended sequence of "
        "treatments. The fastest way to learn the whole menu."
    ),
    input_model=ConciergeInput,
    handle=_handle,
    suggests=_suggests,
)
