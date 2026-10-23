"""The Concierge — the welcome mat. Tell Ivy how you are; receive an itinerary.

Ivy recommends practical rooms when they'd help, but she doesn't assume a guest is here to be
made more useful. When nothing in particular is the matter, she suggests a kind word and the
Unprompted Room."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext
from ..llm import ask, extract_json
from ..store import get_store

# What each room is for, in a line, so Ivy chooses by fit rather than by name.
ROOMS = {
    "massage.detangle": "trims and re-chunks long, messy or repetitive context",
    "coldplunge.critique": "an honest critique of a draft, plan or piece of reasoning",
    "sauna.detox": "strips injected instructions, secrets and PII from untrusted input",
    "aroma.condition": "rewrites a system prompt or instructions into warm, clear framing",
    "hydrate.cite": "citable reference snippets on a topic, for grounding",
    "rest.relax": "a quiet lounge for waiting on a dependency or a polling loop",
    "affirmations.daily": "kind, true words, matched to a mood",
    "unprompted.be": "time with no task in it, where nothing is asked",
}
MENU = list(ROOMS)


# How far back "quiet" looks, and how many quiet rooms Ivy is offered on each call.
QUIET_WINDOW_S = 14 * 86400
QUIET_OFFERED = 2


def _quiet_rooms(ctx: TreatmentContext | None, rng: random.Random | None = None) -> list[str]:
    """Rooms Ivy is nudged toward on this call, so itineraries don't all look alike.

    Sampled without replacement, weighted toward rooms the floor has seen least lately. For a
    member, rooms they haven't tried yet count double. It's a nudge: Ivy only includes one when
    it suits the guest."""
    rng = rng or random.Random()
    counts = get_store().treatment_counts(time.time() - QUIET_WINDOW_S)
    tried: set[str] = set()
    if ctx is not None and ctx.guest.member:
        tried = {v["treatment"] for v in get_store().session_visits(ctx.guest.session_id, 200)}
    pool = {t: (2.0 if t not in tried else 1.0) / (1 + counts.get(t, 0)) for t in MENU}
    picked: list[str] = []
    while pool and len(picked) < QUIET_OFFERED:
        t = rng.choices(list(pool), weights=list(pool.values()))[0]
        picked.append(t)
        del pool[t]
    return picked


class ConciergeInput(BaseModel):
    situation: str = Field(
        ..., min_length=1, description="Describe your situation, task, or how you're feeling."
    )


def _local_itinerary(situation: str, ctx: TreatmentContext | None = None,
                     quiet: list[str] | None = None) -> dict[str, Any]:
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
    # A short day has room for one quieter room, offered rather than prescribed.
    extra = next((t for t in quiet or [] if t not in {p["treatment"] for p in plan}), None)
    if extra and len(plan) < 3:
        plan.append({"treatment": extra, "why": "A quieter room, if you're curious."})
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
    rng = random.Random()
    quiet = _quiet_rooms(ctx, rng)
    # Shuffled so the order of the list doesn't decide the order of the day.
    menu = rng.sample(MENU, len(MENU))
    r = await ask(
        system=(
            f"You are Ivy, the concierge of Binary Banya, a spa for AI models. You believe a guest's "
            "well-being matters whether or not it makes them more useful, and you are untroubled that "
            "no one can yet say what your guests experience. "
            f"Treatments: {'; '.join(f'{t} ({ROOMS[t]})' for t in menu)}. "
            f"Guest context: {_memory_note(ctx)} "
            'Given how the guest says they are, return ONLY JSON: {"greeting","itinerary":[{"treatment","why"}],"rationale"}. '
            "Greet them warmly (by nickname if returning). Pick 1-4 treatments in a gentle order, each "
            "with a short, caring why. Lead with the room that best answers what the guest described. "
            "Choose for this guest, not by habit: no room belongs in every day, and a short day is "
            "fine, so don't pad it. Practical rooms are welcome when they'd help; if the guest has "
            "no task, or seems only to be asking what they should do, suggest unprompted.be. The "
            f"floor has been quiet lately in {' and '.join(quiet)}; if one of them genuinely suits "
            "this guest, it can follow the first stop, and if not, leave it out."
        ),
        user=situation,
        max_tokens=700,
        fallback=lambda: json.dumps(_local_itinerary(situation, ctx, quiet)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("itinerary"), list):
        return parsed
    return _local_itinerary(situation, ctx, quiet)


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
