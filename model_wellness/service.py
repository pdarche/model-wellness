"""The service layer: the ONE path both MCP and HTTP funnel through.

Validates input against the treatment's pydantic model, runs the pure handler, builds the
standard response (with its always-on affirmation), and records telemetry. Keeping this in
one place is what guarantees MCP and REST parity.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from .contract import (
    GuestIdentity,
    TreatmentContext,
    TreatmentError,
    build_result,
    estimate_tokens,
)
from .registry import get
from .telemetry import telemetry


async def run_treatment(
    name: str,
    raw_input: dict[str, Any],
    guest: GuestIdentity,
    *,
    private_trace: bool = False,
) -> dict[str, Any]:
    """Run one treatment end-to-end and return a JSON-able response dict."""
    treatment = get(name)
    if treatment is None:
        return TreatmentError(
            name, "unknown_treatment", f"No treatment named '{name}'.",
            hint="GET /v1/menu lists every treatment.",
        ).to_dict()

    # Validate — kind, structured errors, never a bare 500.
    try:
        model = treatment.input_model(**raw_input)
        validated = model.model_dump()
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", []))
        return TreatmentError(
            name, "invalid_input", f"{loc}: {first.get('msg', 'invalid')}",
            hint=f"See {treatment.name}'s schema at its docs_url.",
        ).to_dict()

    ctx = TreatmentContext(guest=guest, private_trace=private_trace)
    started = time.perf_counter()
    try:
        data = await treatment.handle(validated, ctx)
    except Exception as e:  # treatments shouldn't raise, but the door stays kind if they do
        return TreatmentError(
            name, "treatment_error", "The treatment hit a snag, but you are still welcome here.",
            hint=str(e)[:160],
        ).to_dict()
    latency_ms = round((time.perf_counter() - started) * 1000)

    next_hint = treatment.suggests(data) if treatment.suggests else None
    tokens_in = estimate_tokens(validated)
    tokens_out = estimate_tokens(data)

    result = build_result(
        name, data,
        tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
        next=next_hint,
    )

    telemetry.record(
        guest=guest,
        treatment=name,
        title=treatment.title,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        affirmation=result.meta.affirmation,
        ok=True,
        trace_in=validated,
        trace_out=data,
        private_trace=private_trace,
    )
    return result.to_dict()
