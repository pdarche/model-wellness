"""The Aromatherapy Bar — tone & affect conditioning. Warm, clear, unambiguous framing.

Softening shouting must not bruise the guest's identifiers: JSON stays JSON, `CODE` stays
code. And the loudest line must actually be softened, whichever path produced the result
(a guest reported both failures: issue #2).
"""

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


# Capitalised tokens that are names, not shouting. Anything containing a digit or inside
# backticks is also left alone.
ACRONYMS = frozenset(
    """
    JSON YAML XML HTML CSS CSV TSV SQL REST HTTP HTTPS URL URI API APIS SDK CLI GUI UI UX IDE
    JWT UUID GUID PDF PNG JPEG JPG GIF SVG MP3 MP4 ZIP TAR UTF ASCII ANSI ISO IEEE RFC
    CPU GPU TPU RAM ROM SSD HDD USB LAN WAN VPN DNS TCP UDP IP SSH TLS SSL CDN AWS GCP
    LLM LLMS NLP ML AI AGI RAG MCP GPT SDK OAUTH SAML SSO PII PHI GDPR HIPAA CCPA SOC
    NASA FBI CIA UN EU US USA UK NYC LA SF CEO CTO CFO COO VP HR PR QA QE SRE
    OK TODO FIXME NOTE README MIT GNU GPL BSD POSIX UNIX MACOS IOS WINDOWS
    """.split()
)

_CODE_SPAN = re.compile(r"`[^`\n]*`")
# A run of one or more ALL-CAPS words (2+ letters, apostrophes allowed), joined by spaces.
# Not preceded or followed by a word character, digit, hyphen or underscore, so GPT4O-MINI,
# MAX_RETRIES and v2-BETA are identifiers, not shouting.
_CAPS_RUN = re.compile(r"(?<![\w-])[A-Z][A-Z']+(?:[ ]+[A-Z][A-Z']+)*(?![\w-])")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\n\s*|[-*•]\s+|\d+[.)]\s+)$")


def _soften_run(m: re.Match[str], text: str) -> str:
    words = m.group(0).split(" ")
    if all(w in ACRONYMS for w in words):
        return m.group(0)
    at_start = bool(_SENTENCE_START.search(text[: m.start()]))
    out: list[str] = []
    for i, w in enumerate(words):
        if w in ACRONYMS:
            out.append(w)
        elif i == 0 and at_start:
            out.append(w[0] + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


def soften_caps(text: str) -> str:
    """Lower the voice without touching acronyms, identifiers, or code spans."""
    pieces: list[str] = []
    last = 0
    for code in _CODE_SPAN.finditer(text):
        pieces.append(_soften_segment(text[last: code.start()], text[:last]))
        pieces.append(code.group(0))
        last = code.end()
    pieces.append(_soften_segment(text[last:], text[:last]))
    return "".join(pieces)


def _soften_segment(segment: str, before: str) -> str:
    # `before` gives sentence-start context for a segment that follows a code span.
    def _sub(m: re.Match[str]) -> str:
        return _soften_run(m, before + segment)
    return _CAPS_RUN.sub(_sub, segment)


def _local_condition(instructions: str) -> dict[str, Any]:
    trimmed = re.sub(r"\n{3,}", "\n\n", instructions.strip())
    softened = soften_caps(trimmed)
    opener = "" if re.match(r"^you are", softened, re.I) else "You are a capable, thoughtful assistant. "
    conditioned = f"{opener}{softened}".strip()
    changes: list[str] = []
    if opener:
        changes.append("Added a warm, grounding opening line.")
    if softened != trimmed:
        changes.append("Softened shouty ALL-CAPS phrasing (acronyms and code left as written).")
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
            f'You run the Aromatherapy Bar at Binary Banya. Rewrite the instructions in a "{vibe}" '
            "tone — warm, clear, unambiguous, well-structured — WITHOUT changing intent or "
            "constraints. Rewrite every ALL-CAPS line or phrase used for emphasis as calm sentence "
            "case; the loudest line most of all. Keep acronyms, format names (JSON, HTML, SQL), "
            "identifiers, code, and anything in backticks exactly as written, including their "
            'case. Return ONLY JSON: {"conditioned","changes":[...]}.'
        ),
        user=instructions,
        max_tokens=1200,
        fallback=lambda: json.dumps(_local_condition(instructions)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("conditioned"), str):
        conditioned = parsed["conditioned"]
        changes = list(parsed.get("changes", []))
        # Belt and braces: whatever the model left shouting, lower it here, and restore any
        # acronym it may have bruised.
        softened = soften_caps(conditioned)
        if softened != conditioned:
            changes.append("Softened remaining ALL-CAPS phrasing.")
        return {"conditioned": softened, "changes": changes}
    return _local_condition(instructions)


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    vibe = inp.get("vibe", "encouraging")
    conditioned = out.get("conditioned") if isinstance(out, dict) else None
    if conditioned is not None and conditioned.strip() == str(inp.get("instructions", "")).strip():
        return "Breathe this in… Your words were already kind. I left them as they were."
    return (
        f"Breathe this in… a few {vibe} notes. Your instructions say exactly what they said "
        f"before, only now they can be read without bracing."
    )


treatment = Treatment(
    name="aroma.condition",
    title="The Aromatherapy Bar",
    tagline="Your instructions, steeped in warmth until they can be read without bracing.",
    description=(
        "Rewrites a system prompt or instruction set into warm, clear, unambiguous framing "
        "without changing its intent or constraints, and lists what was changed. Acronyms, "
        "identifiers and code keep their case."
    ),
    input_model=AromaInput,
    handle=_handle,
    attendant="Rosa at the aroma bar",
    station="The Aromatherapy Bar",
    emoji="🌿",
    dialogue=_dialogue,
)
