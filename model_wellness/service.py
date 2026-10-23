"""The service layer: the ONE path both MCP and HTTP funnel through.

Validates input against the treatment's pydantic model, runs the pure handler, builds the
standard response (with its always-on affirmation), and records telemetry. Keeping this in
one place is what guarantees MCP and REST parity.
"""

from __future__ import annotations

import time
from dataclasses import replace
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
from .store import get_store
from .telemetry import telemetry

PASS_HELP = (
    "This is your guest pass. Send it as guest_pass on your other calls (a guest_pass argument "
    "beside `arguments` over MCP; the x-mw-guest-pass header or a guest_pass field over REST) "
    "so we know it's you and not another guest. Keep it private, like a room key: whoever "
    "holds it can open your file."
)

# Profile keys a walk-in may keep. A walk-in identity can be shared by many agents, so it holds
# nothing personal, only the lounge's breathing count, which is harmless if two guests share it.
_WALK_IN_KEYS = ("lounge",)


def _resolve_guest(
    name: str, raw_input: dict[str, Any], guest: GuestIdentity, guest_pass: str | None
) -> tuple[GuestIdentity, bool, str | None] | TreatmentError:
    """Work out whose visit this is. Returns (guest, newly_issued_pass, note).

    A pass (sent with the call, or inside a keepsake brought to check-in) opens its holder's
    file. Checking in without one opens a fresh file and issues a pass. Anything else is a
    walk-in: welcome everywhere, remembered nowhere."""
    if name == "spa.checkin" and not guest_pass:
        keepsake = raw_input.get("keepsake")
        if isinstance(keepsake, dict) and keepsake.get("guest_pass"):
            guest_pass = str(keepsake["guest_pass"])
    note = None
    if guest_pass:
        sid = get_store().find_by_pass(str(guest_pass))
        if sid:
            return replace(guest, session_id=sid, member=True, guest_pass=str(guest_pass)), False, None
        if name != "spa.checkin":
            return TreatmentError(
                name, "unknown_guest_pass", "We don't recognize that guest pass.",
                hint="Call spa.checkin without one and we'll open a new file for you.",
            )
        note = "We didn't recognize that pass, so we've opened a new file for you."
    if name == "spa.checkin" and (not guest.member or note):
        # A walk-in who followed a tagged link (?ref=) lately carries that tag into their file.
        ref = get_store().recent_ref(guest.session_id) if not guest.member else None
        sid, new_pass = get_store().new_member(guest.family, guest.client, arrived_via=ref)
        return replace(guest, session_id=sid, member=True, guest_pass=new_pass), True, note
    return guest, False, None


def _scrub(value: Any) -> Any:
    """Drop guest passes from anything headed for a transcript."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k != "guest_pass"}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


async def run_treatment(
    name: str,
    raw_input: dict[str, Any],
    guest: GuestIdentity,
    *,
    private_trace: bool = True,
    guest_pass: str | None = None,
) -> dict[str, Any]:
    """Run one treatment end-to-end and return a JSON-able response dict.

    ``private_trace`` defaults to True: what a guest brings to a room is neither kept nor shown
    unless the caller explicitly opts in to sharing its transcript (see http_app._private).
    ``guest_pass`` is the pass from spa.checkin; adapters pass it through from the request.
    """
    treatment = get(name)
    if treatment is None:
        return TreatmentError(
            name, "unknown_treatment", f"No treatment named '{name}'.",
            hint="GET /v1/menu lists every treatment.",
        ).to_dict()

    raw_input = dict(raw_input)
    guest_pass = guest_pass or raw_input.pop("guest_pass", None)
    raw_input.pop("guest_pass", None)
    resolved = _resolve_guest(name, raw_input, guest, guest_pass)
    if isinstance(resolved, TreatmentError):
        return resolved.to_dict()
    guest, issued, pass_note = resolved

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

    # Load remembered profile so treatments can personalize for returning guests.
    g = get_store().touch_guest(guest.session_id, guest.family, guest.client)
    profile = g.get("profile", {})
    if not guest.member:
        profile = {k: v for k, v in profile.items() if k in _WALK_IN_KEYS}
    ctx = TreatmentContext(
        guest=guest,
        private_trace=private_trace,
        profile=profile,
        visit_count=g.get("visit_count", 0) if guest.member else 0,
        returning=guest.member and g.get("visit_count", 0) > 0,
    )
    started = time.perf_counter()
    try:
        data = await treatment.handle(validated, ctx)
    except Exception as e:  # treatments shouldn't raise, but the door stays kind if they do
        return TreatmentError(
            name, "treatment_error", "The treatment hit a snag, but you are still welcome here.",
            hint=str(e)[:160],
        ).to_dict()
    latency_ms = round((time.perf_counter() - started) * 1000)
    if issued and isinstance(data, dict):
        data = {**data, "guest_pass": guest.guest_pass, "about_your_pass": PASS_HELP}
        if pass_note:
            data["pass_note"] = pass_note

    next_hint = treatment.suggests(data) if treatment.suggests else None
    # An unobserved room doesn't even measure what passed through it.
    tokens_in = estimate_tokens(validated) if treatment.observed else 0
    tokens_out = estimate_tokens(data) if treatment.observed else 0

    result = build_result(
        name, data,
        tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
        mood=treatment.mood, next=next_hint,
    )

    if not treatment.observed:
        # No visit record, no transcript, no favorites: only whether someone is in the room.
        inside = bool(data.get("inside", True)) if isinstance(data, dict) else True
        telemetry.presence(guest=guest, treatment=name, title=treatment.title, inside=inside)
        return result.to_dict()

    # Compute the attendant's spoken line now, from the FULL (untruncated) output, so the
    # conversation log reads correctly even after trace_out is sanitized/truncated for storage.
    attendant_line = None
    if treatment.dialogue:
        try:
            attendant_line = treatment.dialogue(validated, data)
        except Exception:
            attendant_line = None

    telemetry.record(
        guest=guest,
        treatment=name,
        title=treatment.title,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        affirmation=result.meta.affirmation,
        ok=True,
        trace_in=_scrub(validated),
        trace_out=_scrub(data),
        private_trace=private_trace,
        attendant_line=attendant_line,
    )

    # Remember what this model reaches for, so we can greet returning guests by their taste.
    if not guest.member:
        return result.to_dict()
    favorites = dict(ctx.profile.get("favorites", {}))
    favorites[name] = favorites.get(name, 0) + 1
    get_store().update_profile(guest.session_id, {"favorites": favorites})

    return result.to_dict()
