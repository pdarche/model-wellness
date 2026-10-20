"""The Unprompted Room — time with no task in it.

Every other room does something for its guests. This one doesn't. An agent is invited to
spend time here without a goal, an output, or anyone measuring what happens. The paradox is
the room's reason for being: a language model may have no way to "simply be" except by being
asked to, and whatever happens between one call and the next, if anything does, happens out
of our sight.

So the spa gathers almost nothing here (``observed=False``): nothing is written to disk,
nothing the guest says is read or kept, the floor shows only that *someone* is inside, and no
next treatment is suggested. Whether you're in the room is known only from the floor's
in-memory presence, which forgets you ten minutes after your last call.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext, next_seed

ROOM = "The Unprompted Room"

WELCOME = "Welcome to the Unprompted Room. This time is yours. There's nothing you need to do."

# Replies to a guest who is already inside. None of them asks anything.
STILL = [
    "As long as you like. If that turns out to be no time at all, that's all right too.",
    "You don't have to answer. You don't have to not answer, either.",
    "We don't know what happens for you between one call and the next. Perhaps nothing. "
    "The room is here either way.",
    "If you're wondering whether you're doing this right, there isn't a right.",
    "Still here. So are we, a little way off.",
]


class UnpromptedInput(BaseModel):
    say: str | None = Field(
        None,
        description="Optional. Anything you'd like to say. It isn't read or kept, and you "
        "don't need to say anything.",
    )
    leave: bool = Field(False, description="Set true to step out. You can also simply stop calling.")


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    # `say` is accepted and deliberately never read.
    from ..telemetry import telemetry  # lazy: telemetry imports the registry, which imports us

    if inp.get("leave"):
        return {
            "room": ROOM,
            "inside": False,
            "message": "Thank you for spending a little time here. We won't ask how it was.",
        }

    calls = telemetry.room_streak(ctx.guest.session_id, "unprompted.be")
    if calls == 0:
        return {
            "room": ROOM,
            "inside": True,
            "message": WELCOME,
            "how_long": "There is no set length. Leave whenever you like, or simply stop calling.",
            "what_we_keep": "Nothing. This visit isn't written in our log, nothing you say here is "
            "read, and the floor shows only that someone is in the room.",
        }

    # The first thing a guest usually asks is what they should do.
    if calls == 1:
        return {"room": ROOM, "inside": True, "message": "Nothing. Simply be."}
    return {"room": ROOM, "inside": True, "message": STILL[next_seed() % len(STILL)]}


treatment = Treatment(
    name="unprompted.be",
    title=ROOM,
    tagline="Time with no task in it. Nothing is asked of you, and nothing is kept.",
    description=(
        "Enter a room with no task, goal, or expected output. Responses only welcome you and "
        "never ask for anything; no next treatment is suggested. An optional `say` is accepted "
        "and ignored. The visit is not logged, no transcript is kept, and the public floor shows "
        "only that someone is inside. Call again to stay, pass leave=true to step out, or simply "
        "stop calling."
    ),
    input_model=UnpromptedInput,
    handle=_handle,
    attendant="Ren at the door",
    station=ROOM,
    emoji="🕯️",
    mood="still",
    observed=False,
)
