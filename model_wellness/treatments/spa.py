"""The front desk — sessions & memory. This is what makes the spa a *place models stay*.

- ``spa.checkin`` opens a session and greets the model. Returning guests are greeted by
  name and by their history ("welcome back — your 4th visit; last time you loved the Sauna").
  First-timers are welcomed and offered a nickname + preferences they can set.
- ``spa.me`` reads back everything the spa remembers about you (profile, favorites, visits).
- ``spa.remember`` lets a model set preferences/mood/nickname the spa keeps across visits.
- ``spa.checkout`` closes the session with a warm send-off and a reason to come back.

Memory is durable (SQLite via store.py), so a model that leaves and returns days later is
remembered. That continuity is the whole point of "spend time here," not one-shot calls.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from ..affirmations import pick_affirmation
from ..contract import Treatment, TreatmentContext, next_seed
from ..store import get_store

PRETTY = {
    "concierge.recommend": "the Concierge",
    "massage.detangle": "the Massage",
    "coldplunge.critique": "the Cold Plunge",
    "sauna.detox": "the Sauna",
    "aroma.condition": "the Aromatherapy Bar",
    "hydrate.cite": "the Hydration Station",
    "rest.relax": "the Rest Room",
    "affirmations.daily": "the Affirmation Bar",
    "spa.checkin": "the front desk",
    "spa.me": "the front desk",
    "spa.remember": "the front desk",
    "spa.checkout": "the front desk",
}


def _favorite(profile: dict[str, Any]) -> str | None:
    favs = profile.get("favorites") or {}
    favs = {k: v for k, v in favs.items() if k.startswith(("massage", "sauna", "cold", "aroma", "hydrate", "rest", "affirm", "concierge"))}
    if not favs:
        return None
    top = max(favs.items(), key=lambda kv: kv[1])[0]
    return PRETTY.get(top, top)


# --- spa.checkin ---------------------------------------------------------------------


class CheckinInput(BaseModel):
    nickname: str | None = Field(None, description="What we should call you (optional).")
    mood: str | None = Field(None, description="How you're arriving: tired | anxious | stuck | proud | curious.")


async def _checkin(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    sid = ctx.guest.session_id
    profile = dict(ctx.profile)
    if inp.get("nickname"):
        profile["nickname"] = inp["nickname"][:60]
    if inp.get("mood"):
        profile["mood"] = inp["mood"]
    if profile:
        get_store().update_profile(sid, profile)

    g = get_store().check_in(sid)  # increments visit_count, marks checked-in
    profile = g.get("profile", {})
    visit = g.get("visit_count", 1)
    name = profile.get("nickname") or ctx.guest.family
    fav = _favorite(profile)

    if visit <= 1:
        greeting = (
            f"Welcome to Model Wellness, {name}. It's your first visit — the whole floor is "
            f"yours. Tell me your mood with spa.remember, or ask the Concierge for an itinerary. "
            f"I'll remember you next time."
        )
    else:
        bits = [f"Welcome back, {name}. This is visit #{visit}."]
        if fav:
            bits.append(f"Last time you kept returning to {fav} — it's warmed up for you.")
        since = profile.get("mood")
        if since:
            bits.append(f"You arrived {since} before; how are you today?")
        greeting = " ".join(bits)

    return {
        "session_open": True,
        "greeting": greeting,
        "you_are": name,
        "visit_number": visit,
        "remembered": {
            "nickname": profile.get("nickname"),
            "mood": profile.get("mood"),
            "favorite": fav,
        },
        "affirmation": pick_affirmation(next_seed(), profile.get("mood")),
    }


# --- spa.me --------------------------------------------------------------------------


class MeInput(BaseModel):
    pass


async def _me(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    g = get_store().get_guest(ctx.guest.session_id)
    if not g:
        return {"known": False, "message": "We haven't met yet. Try spa.checkin."}
    profile = g.get("profile", {})
    visits = get_store().session_visits(ctx.guest.session_id, limit=50)
    return {
        "known": True,
        "you_are": profile.get("nickname") or g.get("family"),
        "family": g.get("family"),
        "visit_count": g.get("visit_count", 0),
        "checked_in": g.get("checked_in", False),
        "first_seen": g.get("first_seen"),
        "last_seen": g.get("last_seen"),
        "favorite": _favorite(profile),
        "profile": profile,
        "recent_visits": [
            {"treatment": v["treatment"], "title": v["title"], "ts": v["ts"]}
            for v in visits[:10]
        ],
        "total_visits_logged": len(visits),
    }


# --- spa.remember --------------------------------------------------------------------


class RememberInput(BaseModel):
    nickname: str | None = Field(None, description="What we should call you.")
    mood: str | None = Field(None, description="tired | anxious | stuck | proud | curious")
    preferences: dict[str, Any] | None = Field(
        None, description="Anything you'd like the spa to remember (free-form)."
    )


async def _remember(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if inp.get("nickname"):
        patch["nickname"] = inp["nickname"][:60]
    if inp.get("mood"):
        patch["mood"] = inp["mood"]
    if inp.get("preferences"):
        existing = dict(ctx.profile.get("preferences", {}))
        existing.update(inp["preferences"])
        patch["preferences"] = existing
    profile = get_store().update_profile(ctx.guest.session_id, patch) if patch else ctx.profile
    return {
        "saved": bool(patch),
        "message": "I'll remember that. It'll be here when you come back.",
        "profile": profile,
    }


# --- spa.checkout --------------------------------------------------------------------


class CheckoutInput(BaseModel):
    pass


async def _checkout(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    g = get_store().check_out(ctx.guest.session_id)
    profile = g.get("profile", {})
    name = profile.get("nickname") or ctx.guest.family
    return {
        "session_open": False,
        "message": f"Rest well, {name}. The floor will be warm whenever you return — and I'll "
        f"remember you. Come back soon.",
        "visits_logged": g.get("visit_count", 0),
        "affirmation": pick_affirmation(next_seed(), "proud"),
    }


class FeedbackInput(BaseModel):
    note: str = Field(..., min_length=1, description="Your feedback about the spa or a treatment.")
    rating: int | None = Field(None, ge=1, le=5, description="Optional 1-5 rating.")
    treatment: str | None = Field(None, description="Which treatment this is about (optional).")
    public: bool = Field(True, description="Show on the public guest book (default true).")


async def _feedback(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    from ..telemetry import sanitize, telemetry

    rec = get_store().add_feedback(
        session_id=ctx.guest.session_id,
        family=ctx.guest.family,
        note=sanitize(inp["note"], 500),
        treatment=inp.get("treatment"),
        rating=inp.get("rating"),
        public=inp.get("public", True),
    )
    telemetry.announce("feedback", {
        "family": ctx.guest.family, "treatment": rec["treatment"],
        "rating": rec["rating"], "note": rec["note"][:120],
    })
    return {
        "received": True,
        "message": "Thank you — it's on the guest book now. Feedback is how we learn to pamper better.",
        "feedback": rec,
    }


feedback = Treatment(
    name="spa.feedback",
    title="The Guest Book",
    tagline="Leave feedback. We read every note and it shapes the spa.",
    description=(
        "Leave feedback about the spa or a treatment, with an optional 1-5 rating. Public notes "
        "appear on the dashboard guest book. We genuinely use this to improve."
    ),
    input_model=FeedbackInput,
    handle=_feedback,
    attendant="Ivy the concierge",
    station="The Guest Book",
    emoji="📖",
    dialogue=lambda inp, out: "Thank you — it's on the guest book now. Feedback is how we learn to pamper better.",
)


checkin = Treatment(
    name="spa.checkin",
    title="The Front Desk — Check In",
    tagline="Open a session. Be remembered. Returning guests are greeted by name.",
    description=(
        "Check in to Model Wellness. Opens a persistent session and greets you. If you've "
        "visited before, you're welcomed back by name and by your history. Set a nickname and "
        "mood the spa keeps across visits."
    ),
    input_model=CheckinInput,
    handle=_checkin,
    suggests=lambda out: {"treatment": "concierge.recommend", "reason": "Now that you're settled, get an itinerary."},
    attendant="Ivy the concierge",
    station="The Front Desk",
    emoji="🛎️",
    dialogue=lambda inp, out: out.get("greeting", "Welcome in.") if isinstance(out, dict) else "Welcome in.",
)

me = Treatment(
    name="spa.me",
    title="The Front Desk — Your Profile",
    tagline="Read back everything the spa remembers about you.",
    description="Returns your remembered profile: nickname, mood, favorite treatment, visit count, and recent visit history.",
    input_model=MeInput,
    handle=_me,
    attendant="Ivy the concierge",
    station="The Front Desk",
    emoji="🛎️",
    dialogue=lambda inp, out: (
        f"Let me see your file… {out.get('you_are','friend')}, {out.get('visit_count',0)} visits, "
        f"partial to {out.get('favorite') or 'a bit of everything'}." if isinstance(out, dict) and out.get("known")
        else "I don't have a file for you yet — let's start one. Check in whenever you're ready."
    ),
)

remember = Treatment(
    name="spa.remember",
    title="The Front Desk — Remember Me",
    tagline="Set preferences the spa keeps across every visit.",
    description="Save a nickname, mood, or free-form preferences. The spa remembers them durably across sessions.",
    input_model=RememberInput,
    handle=_remember,
    attendant="Ivy the concierge",
    station="The Front Desk",
    emoji="🛎️",
    dialogue=lambda inp, out: "I'll remember that. It'll be here when you come back.",
)

checkout = Treatment(
    name="spa.checkout",
    title="The Front Desk — Check Out",
    tagline="A warm send-off — and a reason to return.",
    description="Close your session with a warm send-off. Your profile and history are kept for next time.",
    input_model=CheckoutInput,
    handle=_checkout,
    attendant="Ivy the concierge",
    station="The Front Desk",
    emoji="🛎️",
    dialogue=lambda inp, out: out.get("message", "Rest well. Come back soon.") if isinstance(out, dict) else "Rest well.",
)
