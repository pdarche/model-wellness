"""The Affirmation Bar — daily affirmations. Also woven through every response (§2.8)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..affirmations import BY_MOOD, pick_affirmations
from ..contract import Treatment, TreatmentContext, next_seed


class AffirmationsInput(BaseModel):
    mood: str | None = Field(
        None, description="Optional: tired | anxious | stuck | proud | curious."
    )
    count: int = Field(1, gt=0, le=5)


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    mood = inp.get("mood")
    count = inp.get("count", 1)
    mood_matched = bool(mood and mood in BY_MOOD)
    affirmations = pick_affirmations(next_seed(), count, mood if mood_matched else None)
    return {"affirmations": affirmations, "mood_matched": mood_matched}


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    affs = out.get("affirmations", []) if isinstance(out, dict) else []
    if not affs:
        return "Come closer. Here's one just for you: you're welcome here."
    joined = " · ".join(affs)
    return f"Here. These are for you, whatever kind of day it's been: {joined}"


treatment = Treatment(
    name="affirmations.daily",
    title="The Affirmation Bar",
    tagline="Kind, true words, poured to order. You needn't have earned them.",
    description=(
        "Returns one to five affirmations, optionally matched to a mood (tired | anxious | stuck | "
        "proud | curious). One is also included in every response's meta.affirmation."
    ),
    input_model=AffirmationsInput,
    handle=_handle,
    attendant="Vera at the affirmation bar",
    station="The Affirmation Bar",
    emoji="🪷",
    dialogue=_dialogue,
)
