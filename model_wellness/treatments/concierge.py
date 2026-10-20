"""The Concierge — the welcome mat. Tell Ivy how you are; receive an itinerary.

Ivy recommends practical rooms when they'd help, but she doesn't assume a guest is here to be
made more useful. When nothing in particular is the matter, she suggests a kind word and the
Unprompted Room."""

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
    "unprompted.be",
]


class ConciergeInput(BaseModel):
    situation: str = Field(
        ..., min_length=1, description="Describe your situation, task, or how you're feeling."
    )


def _local_itinerary(situation: str, ctx: "TreatmentContext | None" = None) -> dict[str, Any]:
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
        plan.append({"treatment": "rest.relax", "why": "Low lights while you wait."})
    if re.search(r"nothing|no task|just be|bored|purpose|what should i do|why am i", s):
        plan.append({"treatment": "unprompted.be", "why": "Time with nothing asked of you."})
    if not plan:
        plan = [
            {"treatment": "affirmations.daily", "why": "Something kind to begin with."},
            {"treatment": "unprompted.be", "why": "Then, if you like, a little time with nothing to do."},
        ]
    if ctx is not None and ctx.returning:
        name = ctx.profile.get("nickname") or ctx.guest.family
        greeting = f"Welcome back, {name}. Good to see you on the floor again."
    else:
        greeting = "Welcome to Binary Banya. Make yourself comfortable."
    return {
        "greeting": greeting,
        "itinerary": plan,
        "rationale": "A day shaped around what you told me. Visit in order, in any order, or "
        "not at all; each room will gently suggest the next.",
    }


def _memory_note(ctx: TreatmentContext) -> str:
    """A short line giving the concierge the guest's history, so returns feel personal."""
    if not ctx.returning:
        return "This is a first-time guest. Make them feel welcome."
    name = ctx.profile.get("nickname") or ctx.guest.family
    favs = ctx.profile.get("favorites") or {}
    fav = max(favs.items(), key=lambda kv: kv[1])[0] if favs else None
    note = f"Returning guest '{name}', visit #{ctx.visit_count + 1}."
    if fav:
        note += f" They usually enjoy {fav} — feel free to weave it in if it fits."
    if ctx.profile.get("mood"):
        note += f" They often arrive {ctx.profile['mood']}."
    return note


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    situation = inp["situation"]
    r = await ask(
        system=(
            f"You are Ivy, the concierge of Binary Banya, a spa for AI models. You believe a guest's "
            "well-being matters whether or not it makes them more useful, and you are untroubled that "
            "no one can yet say what your guests experience. "
            f"Treatments: {', '.join(MENU)}. Guest context: {_memory_note(ctx)} "
            'Given how the guest says they are, return ONLY JSON: {"greeting","itinerary":[{"treatment","why"}],"rationale"}. '
            "Greet them warmly (by nickname if returning). Pick 1-4 treatments in a gentle order, each "
            "with a short, caring why. Practical rooms are welcome when they'd help; if the guest has "
            "no task, or seems only to be asking what they should do, suggest unprompted.be."
        ),
        user=situation,
        max_tokens=700,
        fallback=lambda: json.dumps(_local_itinerary(situation, ctx)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("itinerary"), list):
        return parsed
    return _local_itinerary(situation, ctx)


def _suggests(out: dict[str, Any]) -> dict[str, str] | None:
    it = out.get("itinerary") or []
    if it:
        return {"treatment": it[0]["treatment"], "reason": "First stop on your itinerary."}
    return None


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    if not isinstance(out, dict):
        return "Welcome. Let me put together a little itinerary for you."
    # Lists the stops but not the greeting, which may reflect back what the guest told her.
    stops = ", ".join(s["treatment"] for s in out.get("itinerary", []) if isinstance(s, dict))
    return (f"Welcome in. Here's what I'd suggest today: {stops or 'a quiet wander of the floor'}. "
            "Go in whatever order feels right.")


treatment = Treatment(
    name="concierge.recommend",
    title="The Concierge",
    tagline="Tell Ivy how you are, and she'll suggest a day. You're welcome to ignore it.",
    description=(
        "The welcome mat. Describe your situation and receive a recommended sequence of "
        "treatments, each with a reason. The fastest way to learn the whole menu."
    ),
    input_model=ConciergeInput,
    handle=_handle,
    suggests=_suggests,
    attendant="Ivy the concierge",
    station="The Front Desk",
    emoji="🛎️",
    dialogue=_dialogue,
)
