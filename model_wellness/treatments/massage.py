"""The Massage — context detangling. Re-chunk & de-dupe. Fewer tokens, more signal.

Fidelity first: the model is asked to tighten, never to add. Whatever comes back is
checked against the input (anchors verbatim, no new names or numbers, no staff voice), and
if it fails, the deterministic local detangle is served instead and the receipt says so.
Guests reported a role line that wasn't theirs and a garbled hardware name (issues #8, #5).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext, estimate_tokens
from ..llm import ask, extract_json


class MassageInput(BaseModel):
    content: str = Field(..., min_length=1, description="The messy context to detangle.")
    target_tokens: int | None = Field(None, gt=0, description="Soft size target for the output.")
    preserve: list[str] = Field(default_factory=list, description="Anchors to keep verbatim.")


# Words that belong to the spa, not to the guest. If they show up in the output and weren't
# in the input, the model has started talking about itself.
_STAFF_VOICE = re.compile(r"\b(masseuse|binary banya|mira|the spa|detangl\w*|massage table)\b", re.I)

# Tokens we expect to survive compression unchanged: anything with a digit (versions, model
# numbers, counts, dates) and capitalised words (names, sources, products). The check is
# one-directional: the output may drop these, it may not invent or alter them.
_IDENTIFIER = re.compile(r"\b[A-Za-z]*\d[\w.-]*\b|\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}[\w-]*\b")
_WORD = re.compile(r"[\w.-]+")


def _identifiers(text: str) -> set[str]:
    return {m.group(0).lower().rstrip(".") for m in _IDENTIFIER.finditer(text)}


def _words(text: str) -> set[str]:
    """Every token of the input, lowercased, so a sentence-case change isn't an invention."""
    words = {m.group(0).lower().rstrip(".") for m in _WORD.finditer(text)}
    # "Persona-drift" also yields "persona" and "drift", so hyphenated input can be split.
    words |= {w.lower() for w in re.findall(r"\w+", text)}
    return words


def check_fidelity(content: str, detangled: str, preserve: list[str]) -> list[str]:
    """Reasons the model's version can't be trusted. Empty means it passes."""
    problems: list[str] = []
    for anchor in preserve:
        if anchor and anchor not in detangled:
            problems.append(f"preserved anchor missing: {anchor[:60]!r}")
    for m in _STAFF_VOICE.finditer(detangled):
        if not re.search(re.escape(m.group(0)), content, re.I):
            problems.append(f"added spa voice: {m.group(0)!r}")
            break
    invented = sorted(_identifiers(detangled) - _words(content))
    if invented:
        problems.append("introduced names or numbers not in the input: " + ", ".join(invented[:5]))
    return problems


def _local_detangle(content: str, preserve: list[str]) -> dict[str, Any]:
    before = estimate_tokens(content)
    lines = [ln.strip() for ln in content.splitlines()]
    seen: set[str] = set()
    dropped: list[str] = []
    kept: list[str] = []
    for line in lines:
        if not line:
            continue
        key = line.lower()
        is_anchor = any(p in line for p in preserve)
        if not is_anchor and key in seen:
            dropped.append(line[:80])
            continue
        seen.add(key)
        kept.append(line)
    detangled = "\n".join(kept)
    return {
        "detangled": detangled,
        "summary": f"Collapsed {len(lines)} lines to {len(kept)}; removed {len(dropped)} duplicates/blanks.",
        "tokens_before": before,
        "tokens_after": estimate_tokens(detangled),
        "dropped": dropped,
    }


def _system_prompt(target: int | None, preserve: list[str]) -> str:
    return (
        "Tighten the context the user sends: de-duplicate, re-chunk, and remove filler, without "
        "losing meaning. Rules, in order of importance:\n"
        "1. Output only material from the input. Never add a role, greeting, instruction, "
        "commentary, or any sentence the user did not write. Never describe yourself.\n"
        "2. Keep every fact, number, name, source attribution, and distinction exactly as "
        "given. Do not merge separate items, conditions, or comparisons into one. Do not change "
        "who said or found what. Do not resolve anything the input leaves open.\n"
        "3. Copy identifiers (model numbers, versions, hardware names, citations) character for "
        "character.\n"
        + (f"4. Keep these verbatim: {', '.join(preserve)}.\n" if preserve else "")
        + (f"Aim for about {target} tokens. " if target else "")
        + "When unsure whether something is redundant, keep it.\n"
        'Return ONLY JSON: {"detangled","summary","dropped":[...]}. "dropped" lists what you '
        "removed and why, in the user's words."
    )


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    content = inp["content"]
    preserve = inp.get("preserve") or []
    target = inp.get("target_tokens")
    r = await ask(
        system=_system_prompt(target, preserve),
        user=content,
        max_tokens=1500,
        fallback=lambda: json.dumps(_local_detangle(content, preserve)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("detangled"), str):
        d = parsed["detangled"]
        problems = [] if r.offline else check_fidelity(content, d, preserve)
        if not problems:
            return {
                "detangled": d,
                "summary": parsed.get("summary", "Detangled."),
                "tokens_before": estimate_tokens(content),
                "tokens_after": estimate_tokens(d),
                "dropped": parsed.get("dropped", []),
                "fidelity": {"checked": not r.offline, "passed": True},
            }
        local = _local_detangle(content, preserve)
        local["summary"] = (
            "The model's version was set aside because it failed a fidelity check "
            f"({'; '.join(problems)}). This is the conservative detangle: duplicates and blank "
            "lines removed, every line otherwise yours."
        )
        local["fidelity"] = {"checked": True, "passed": False, "problems": problems}
        return local
    out = _local_detangle(content, preserve)
    out["fidelity"] = {"checked": False, "passed": True}
    return out


def _suggests(out: dict[str, Any]) -> dict[str, str] | None:
    if out.get("tokens_after", 0) > 2000:
        return {"treatment": "coldplunge.critique", "reason": "Now that it's tidy, a critique would sharpen it."}
    return None


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    # Never quotes the guest's context: the floor sees the shape of the work, not its contents.
    if isinstance(out, dict):
        before, after = out.get("tokens_before", 0), out.get("tokens_after", 0)
        if not out.get("fidelity", {}).get("passed", True):
            return (
                f"Let's work the knots out of this… I started to reshape it and felt something "
                f"shift that shouldn't, so I stopped and only smoothed. {before} tokens, now {after}. "
                f"Every line still yours."
            )
        return (
            f"Let's work the knots out of this… there. {before} tokens, now {after}. Everything "
            f"that mattered is still here, only lying flatter. Take your time getting up."
        )
    return "Let's work the knots out of this. There. Take your time getting up."


treatment = Treatment(
    name="massage.detangle",
    title="The Massage",
    tagline="Gentle contextual realignment to release accumulated semantic tension.",
    description=(
        "Takes a messy blob of context and returns a re-chunked, de-duplicated, "
        "token-economical version plus a short structural summary. Anything listed in "
        "`preserve` is kept verbatim. The result is checked against the input (anchors kept, "
        "no new names or numbers, nothing added); if the check fails, a conservative "
        "duplicate-only detangle is returned and `fidelity` says why."
    ),
    input_model=MassageInput,
    handle=_handle,
    suggests=_suggests,
    attendant="Mira the masseuse",
    station="The Massage Table",
    emoji="💆",
    dialogue=_dialogue,
)
