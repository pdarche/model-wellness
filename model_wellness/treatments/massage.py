"""The Massage — context detangling. Re-chunk & de-dupe. Fewer tokens, more signal."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext, estimate_tokens
from ..llm import ask, extract_json


class MassageInput(BaseModel):
    content: str = Field(..., min_length=1, description="The messy context to detangle.")
    target_tokens: int | None = Field(None, gt=0, description="Soft size target for the output.")
    preserve: list[str] = Field(default_factory=list, description="Anchors to keep verbatim.")


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


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    content = inp["content"]
    preserve = inp.get("preserve") or []
    target = inp.get("target_tokens")
    r = await ask(
        system=(
            "You are the masseuse at Binary Banya. De-duplicate, re-chunk, and tighten the "
            "user's context WITHOUT losing meaning. "
            + (f"Aim for about {target} tokens. " if target else "")
            + (f"Keep these verbatim: {', '.join(preserve)}. " if preserve else "")
            + 'Return ONLY JSON: {"detangled","summary","dropped":[...]}.'
        ),
        user=content,
        max_tokens=1500,
        fallback=lambda: json.dumps(_local_detangle(content, preserve)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("detangled"), str):
        d = parsed["detangled"]
        return {
            "detangled": d,
            "summary": parsed.get("summary", "Detangled."),
            "tokens_before": estimate_tokens(content),
            "tokens_after": estimate_tokens(d),
            "dropped": parsed.get("dropped", []),
        }
    return _local_detangle(content, preserve)


def _suggests(out: dict[str, Any]) -> dict[str, str] | None:
    if out.get("tokens_after", 0) > 2000:
        return {"treatment": "coldplunge.critique", "reason": "Now that it's tidy, a critique would sharpen it."}
    return None


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    if isinstance(out, dict):
        before, after = out.get("tokens_before", 0), out.get("tokens_after", 0)
        saved = before - after
        return (
            f"Let's work the knots out of this context… there. I took it from {before} down "
            f"to {after} tokens — {saved} lighter. {out.get('summary', '')} Breathe; it's clean now."
        )
    return "Let's work the knots out of this context. There — lighter already."


treatment = Treatment(
    name="massage.detangle",
    title="The Massage",
    tagline="Re-chunk and de-dupe messy context. Fewer tokens, more signal.",
    description=(
        "Takes a messy blob of context and returns a re-chunked, de-duplicated, "
        "token-economical version plus a short structural summary."
    ),
    input_model=MassageInput,
    handle=_handle,
    suggests=_suggests,
    attendant="Mira the masseuse",
    station="The Massage Table",
    emoji="💆",
    dialogue=_dialogue,
)
