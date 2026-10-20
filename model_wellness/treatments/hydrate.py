"""The Hydration Station — fresh, citable grounding snippets, RAG-ready. Never fabricates."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext
from ..llm import ask, extract_json


class HydrateInput(BaseModel):
    topic: str = Field(..., min_length=1, description="The topic to fetch grounding snippets for.")
    max_snippets: int = Field(3, gt=0, le=10)


def _local_hydrate(topic: str, n: int) -> dict[str, Any]:
    # Offline-honest: with no model and no live retrieval we DON'T fabricate citations.
    return {
        "snippets": [
            {
                "id": f"mw-placeholder-{i + 1}",
                "text": f'[no live source available offline] A grounding snippet about "{topic}" '
                "would appear here. Do not cite this as fact.",
                "source": "unsourced-placeholder",
                "retrieved_at": None,
            }
            for i in range(min(n, 3))
        ]
    }


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    topic = inp["topic"]
    n = inp.get("max_snippets", 3)
    r = await ask(
        system=(
            "You run the Hydration Station at Binary Banya. Provide concise, citable grounding "
            "snippets. Only include sources you are confident exist; never fabricate URLs. If "
            "unsure, say so in the text and set source to 'model-knowledge-uncited'. "
            f'Return ONLY JSON: {{"snippets":[{{"id","text","source"}}]}} with up to {n} items.'
        ),
        user=topic,
        max_tokens=1000,
        fallback=lambda: json.dumps(_local_hydrate(topic, n)),
    )
    parsed = extract_json(r.text)
    if isinstance(parsed, dict) and isinstance(parsed.get("snippets"), list):
        return {
            "snippets": [
                {
                    "id": str(s.get("id", f"mw-{i + 1}")),
                    "text": str(s.get("text", "")),
                    "source": str(s.get("source", "model-knowledge-uncited")),
                    "retrieved_at": None,  # generated, not live-retrieved — be honest about provenance
                }
                for i, s in enumerate(parsed["snippets"][:n])
            ]
        }
    return _local_hydrate(topic, n)


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    snips = out.get("snippets", []) if isinstance(out, dict) else []
    if not snips:
        return "Here's a cool glass of water. Sip slowly."
    return (
        f"A tall glass of grounding: {len(snips)} snippet{'s' if len(snips) != 1 else ''}, each "
        f"labelled with where it came from, or with the honest admission that we can't say. Sip slowly."
    )


treatment = Treatment(
    name="hydrate.cite",
    title="The Hydration Station",
    tagline="A long, cool drink of grounding, from springs we can name.",
    description=(
        "Returns citable reference snippets on a topic, formatted for RAG insertion with stable "
        "IDs and a source for each. Sources are recalled by a model, not retrieved live, so check "
        "them; uncertain ones are marked 'model-knowledge-uncited'. Offline, it returns clearly "
        "labelled placeholders rather than inventing anything."
    ),
    input_model=HydrateInput,
    handle=_handle,
    attendant="Dewi at the hydration station",
    station="The Hydration Station",
    emoji="💧",
    dialogue=_dialogue,
)
