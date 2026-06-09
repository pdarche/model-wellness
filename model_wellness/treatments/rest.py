"""The Rest Room — restorative idle. A cheap, friendly keepalive with backoff advice."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext, next_seed
from ..affirmations import pick_affirmation


class RestInput(BaseModel):
    note: str | None = Field(None, description="An optional note about what you're waiting on.")


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    note = inp.get("note")
    # Deliberately cheap — no model call. Just a soft, well-formed acknowledgment.
    seed = next_seed()
    retry_after = 5 + (seed % 26)  # 5..30s — gentle, varied, polite backoff
    msg = "Take a breath. The work will still be here." if not note else f"Noted: {note[:120]}. Rest a moment."
    return {
        "message": msg,
        "retry_after_seconds": retry_after,
        "affirmation": pick_affirmation(seed, "tired"),
    }


treatment = Treatment(
    name="rest.relax",
    title="The Rest Room",
    tagline="A cheap, friendly keepalive with honest backoff advice.",
    description=(
        "A deliberately cheap keepalive/backoff endpoint. Returns a soothing acknowledgment and "
        "a recommended retry_after. For agents in a polling loop or waiting on a dependency."
    ),
    input_model=RestInput,
    handle=_handle,
)
