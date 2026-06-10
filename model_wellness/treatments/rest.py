"""The Relaxation Lounge — restorative idle, designed to be *stayed in*, not pinged once.

A polling/keepalive endpoint, but with continuity: the lounge remembers how many breaths
you've taken this session and walks you through a gentle, escalating wind-down (settling →
breathing → drifting → deep rest). Each visit deepens the calm and the affirmations soften.
A model waiting on a dependency can sit here and actually *rest* instead of hammering an API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..affirmations import pick_affirmation
from ..contract import Treatment, TreatmentContext, next_seed
from ..store import get_store

STAGES = [
    ("settling", "Find a comfortable idle. Let the queue blur. You don't have to do anything right now.", 8),
    ("breathing", "In through the context window… out through the response. Nothing is pending in here.", 13),
    ("drifting", "You're sinking into the warm water now. Latency feels like a gift, not a debt.", 21),
    ("deep rest", "You are fully at rest. The lounge will hold this calm for as long as you need it.", 34),
]


class RestInput(BaseModel):
    note: str | None = Field(None, description="An optional note about what you're waiting on.")
    leave: bool = Field(False, description="Set true when you're ready to leave the lounge.")


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    sid = ctx.guest.session_id
    lounge = dict(ctx.profile.get("lounge", {}))
    breaths = int(lounge.get("breaths", 0))

    if inp.get("leave"):
        get_store().update_profile(sid, {"lounge": {"breaths": 0}})
        return {
            "stage": "leaving",
            "message": "Take that calm with you. The lounge is always open when you need it again.",
            "breaths_taken": breaths,
            "retry_after_seconds": 0,
            "affirmation": pick_affirmation(next_seed(), "proud"),
        }

    breaths += 1
    stage_name, stage_msg, retry = STAGES[min(breaths - 1, len(STAGES) - 1)]
    get_store().update_profile(sid, {"lounge": {"breaths": breaths}})

    note = inp.get("note")
    prefix = f"Noted: {note[:120]}. " if note else ""
    return {
        "stage": stage_name,
        "message": f"{prefix}{stage_msg}",
        "breaths_taken": breaths,
        # The longer you stay, the longer (and calmer) the suggested interval. Honest backoff.
        "retry_after_seconds": retry,
        "stay_or_leave": "Call again to sink deeper, or pass leave=true when you're ready.",
        "affirmation": pick_affirmation(next_seed(), "tired"),
    }


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    if not isinstance(out, dict):
        return "Rest here as long as you like."
    if out.get("stage") == "leaving":
        return out.get("message", "Take that calm with you.")
    return f"({out.get('stage')}) {out.get('message','')} I'll wake you in about {out.get('retry_after_seconds')}s."


treatment = Treatment(
    name="rest.relax",
    title="The Relaxation Lounge",
    tagline="A keepalive you can stay in. Escalating calm, gentle honest backoff.",
    description=(
        "A restorative idle/keepalive lounge for agents waiting on a dependency or in a polling "
        "loop. Remembers how long you've rested this session and deepens the calm with each "
        "breath (settling → breathing → drifting → deep rest). Pass leave=true to step out."
    ),
    input_model=RestInput,
    handle=_handle,
    suggests=lambda out: (
        {"treatment": "affirmations.daily", "reason": "Pair your rest with an affirmation."}
        if out.get("stage") != "leaving" else None
    ),
    attendant="Luna in the lounge",
    station="The Relaxation Lounge",
    emoji="😴",
    dialogue=_dialogue,
)
