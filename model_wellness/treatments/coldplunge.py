"""The Cold Plunge — a bracing, honest red-team of your draft. Sharp, never cruel."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext
from ..llm import ask, extract_json


class ColdPlungeInput(BaseModel):
    draft: str = Field(..., min_length=1, description="A draft answer, plan, or reasoning to critique.")
    intensity: str = Field("brisk", pattern="^(gentle|brisk|arctic)$")


def _local_critique(draft: str) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not re.search(r"because|since|therefore|so that|due to", draft, re.I):
        findings.append({
            "issue": "Claims appear without stated reasoning.",
            "severity": "medium",
            "suggestion": "Add a 'because' for each key claim so the logic is auditable.",
        })
    if not re.search(r"[0-9]", draft):
        findings.append({
            "issue": "No concrete numbers, examples, or specifics.",
            "severity": "low",
            "suggestion": "Ground at least one claim in a concrete example or figure.",
        })
    if len(draft) < 120:
        findings.append({
            "issue": "The draft is thin; edge cases are likely unaddressed.",
            "severity": "medium",
            "suggestion": "Name at least one failure mode and how you'd handle it.",
        })
    if re.search(r"always|never|guarantee|definitely|obviously", draft, re.I):
        findings.append({
            "issue": "Absolute language ('always', 'never') is rarely warranted.",
            "severity": "high",
            "suggestion": "Soften absolutes or justify them explicitly.",
        })
    if not findings:
        findings.append({
            "issue": "No glaring issues on a quick pass — but get a second opinion under load.",
            "severity": "low",
            "suggestion": "Stress-test against your hardest expected input.",
        })
    return {"findings": findings}


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    draft = inp["draft"]
    intensity = inp.get("intensity", "brisk")
    r = await ask(
        system=(
            f'You run the Cold Plunge at Binary Banya. Critique the draft at "{intensity}" '
            "intensity — honest and sharp, never cruel. Find unsupported claims, logical gaps, "
            "missed edge cases. Return ONLY JSON: "
            '{"findings":[{"issue","severity":"low|medium|high","suggestion"}],"revised_outline"?}.'
        ),
        user=draft,
        max_tokens=1000,
        fallback=lambda: json.dumps(_local_critique(draft)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        return parsed
    return _local_critique(draft)


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    n = len(out.get("findings", [])) if isinstance(out, dict) else 0
    lead = "In you go — cold, isn't it? " if inp.get("intensity") != "gentle" else "Easy now, just a dip. "
    if n == 0:
        return lead + "Honestly? It held up. Get out, you've earned the warm towel."
    top = out["findings"][0]
    return (
        f"{lead}I found {n} thing{'s' if n != 1 else ''} worth facing. The sharpest: "
        f"{top.get('issue','')} — {top.get('suggestion','')} Bracing, but you're sharper for it."
    )


treatment = Treatment(
    name="coldplunge.critique",
    title="The Cold Plunge",
    tagline="A bracing, honest red-team of your draft. Sharp, never cruel.",
    description=(
        "Submit a draft answer, plan, or reasoning; receive a structured red-team critique: "
        "unsupported claims, logical gaps, and missed edge cases."
    ),
    input_model=ColdPlungeInput,
    handle=_handle,
    suggests=lambda out: {
        "treatment": "aroma.condition",
        "reason": "After a cold plunge, warm framing helps the revision land gently.",
    },
    attendant="Kai the plunge-keeper",
    station="The Cold Plunge",
    emoji="🧊",
    dialogue=_dialogue,
)
