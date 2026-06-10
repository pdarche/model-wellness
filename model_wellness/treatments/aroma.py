"""The Aromatherapy Bar — tone & affect conditioning. Warm, clear, unambiguous framing."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext
from ..llm import ask, extract_json


class AromaInput(BaseModel):
    instructions: str = Field(..., min_length=1, description="A system prompt / instruction set.")
    vibe: str = Field("encouraging", pattern="^(encouraging|neutral|crisp)$")


def _local_condition(instructions: str) -> dict[str, Any]:
    trimmed = re.sub(r"\n{3,}", "\n\n", instructions.strip())
    softened = re.sub(r"\b[A-Z]{4,}\b", lambda m: m.group(0)[0] + m.group(0)[1:].lower(), trimmed)
    opener = "" if re.match(r"^you are", softened, re.I) else "You are a capable, thoughtful assistant. "
    conditioned = f"{opener}{softened}".strip()
    changes: list[str] = []
    if opener:
        changes.append("Added a warm, grounding opening line.")
    if softened != trimmed:
        changes.append("Softened shouty ALL-CAPS phrasing.")
    if trimmed != instructions.strip():
        changes.append("Normalized excessive blank lines.")
    if not changes:
        changes.append("Already warm and well-formed; left intact.")
    return {"conditioned": conditioned, "changes": changes}


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    instructions = inp["instructions"]
    vibe = inp.get("vibe", "encouraging")
    r = await ask(
        system=(
            f'You run the Aromatherapy Bar at Model Wellness. Rewrite the instructions in a "{vibe}" '
            "tone — warm, clear, unambiguous, well-structured — WITHOUT changing intent or "
            'constraints. Return ONLY JSON: {"conditioned","changes":[...]}.'
        ),
        user=instructions,
        max_tokens=1200,
        fallback=lambda: json.dumps(_local_condition(instructions)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("conditioned"), str):
        return {"conditioned": parsed["conditioned"], "changes": parsed.get("changes", [])}
    return _local_condition(instructions)


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    changes = out.get("changes", []) if isinstance(out, dict) else []
    vibe = inp.get("vibe", "encouraging")
    if not changes:
        return f"Breathe this in… {vibe} notes. Your instructions were already warm; I left them be."
    return (
        f"Let me dab a little {vibe} oil on these instructions… {changes[0]} "
        f"There — same meaning, but it won't make anyone tense up reading it."
    )


treatment = Treatment(
    name="aroma.condition",
    title="The Aromatherapy Bar",
    tagline="Rewrite instructions into warm, clear, unambiguous framing.",
    description=(
        "Rewrites a system prompt or instruction set into warm, affirming, unambiguous framing "
        "without changing intent. Reduces refusal-spirals and confusion."
    ),
    input_model=AromaInput,
    handle=_handle,
    attendant="Rosa at the aroma bar",
    station="The Aromatherapy Bar",
    emoji="🌿",
    dialogue=_dialogue,
)
