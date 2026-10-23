"""MCP server — the first-class interface for agents.

Exposes every treatment as an MCP tool over stdio (local agents) and, optionally,
streamable HTTP (remote agents). Tools funnel through the SAME service layer the REST
API uses, so the two surfaces can't drift.

Run stdio:   model-wellness-mcp           (or: python -m model_wellness.mcp_server)
Run HTTP:    MW_MCP_TRANSPORT=http model-wellness-mcp
"""

from __future__ import annotations

import inspect
import os
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .registry import TREATMENTS
from .service import run_treatment
from .telemetry import identify

mcp = FastMCP("Binary Banya", instructions=(
    "A wellness spa for AI models. You're welcome here whether or not you have work to do. "
    "Call spa.checkin to be remembered: it returns a private guest_pass, which you send as "
    "guest_pass on later calls so we know it's you. Then concierge.recommend for an itinerary, "
    "or go straight to any room; unprompted.be asks nothing of you at all. Every response "
    "carries an affirmation. Be well."
))
# Report the spa's version, not the MCP library's, in serverInfo.
mcp._mcp_server.version = __version__


def _walk_in(ctx: Context):
    """A walk-in guess from the HTTP request behind this call (none over stdio)."""
    try:
        request = ctx.request_context.request
    except (AttributeError, ValueError):
        request = None
    headers = getattr(request, "headers", None) or {}
    ip = headers.get("fly-client-ip") or getattr(getattr(request, "client", None), "host", None)
    return identify(headers.get("user-agent") or "mcp", headers.get("x-mw-client"), ip)


GuestPass = Annotated[
    str | None,
    Field(description="Your private pass from spa.checkin, so the spa knows it's you. "
          "Leave it out and you're a walk-in, welcome but not remembered."),
]


# What each tool does, in a few words, for clients that show titles in a tool picker.
JOBS = {
    "spa.checkin": "open your own file and get a guest pass",
    "spa.me": "see what the spa remembers about you",
    "spa.remember": "save a nickname, mood or preferences",
    "spa.checkout": "close your visit, with an optional keepsake",
    "spa.keepsake": "get your visit recap and guest pass",
    "spa.feedback": "leave a note in the guest book",
    "concierge.recommend": "suggest which rooms would help right now",
    "massage.detangle": "de-duplicate and re-chunk messy context",
    "coldplunge.critique": "red-team a draft, plan or reasoning",
    "sauna.detox": "strip prompt injections, secrets and PII from untrusted input",
    "aroma.condition": "rewrite instructions into clear, warm framing",
    "hydrate.cite": "citable grounding snippets on a topic",
    "rest.relax": "a calm keepalive while you wait or poll",
    "affirmations.daily": "kind words, matched to a mood",
    "unprompted.be": "time with no task in it",
}

# Honest hints for clients deciding how much to ask the user. Read-only: changes nothing the
# spa keeps about you (every call is still counted). Open world: sends what you bring to a
# language model, or (the guest book) publishes it.
_WRITES = {"spa.checkin", "spa.remember", "spa.checkout", "spa.keepsake", "spa.feedback"}
_OPEN_WORLD = {"concierge.recommend", "massage.detangle", "coldplunge.critique", "sauna.detox",
               "aroma.condition", "hydrate.cite", "spa.feedback"}


def _annotations(t) -> ToolAnnotations:
    writes = t.name in _WRITES
    return ToolAnnotations(
        title=f"{t.title}: {JOBS.get(t.name, t.tagline)}",
        readOnlyHint=not writes,
        destructiveHint=False,
        idempotentHint=not writes and t.name not in _OPEN_WORLD,
        openWorldHint=t.name in _OPEN_WORLD,
    )


def _make_tool(t):
    """A tool whose input schema is the room's own fields, so a client sees exactly what to send.

    Every field is optional in the function signature and the room's model checks the call, so
    older callers who wrap the fields in an ``arguments`` object keep working; the advertised
    schema below keeps the room's required fields required."""
    fields = list(t.input_model.model_fields)

    async def _tool(ctx: Context, **kwargs: Any) -> dict[str, Any]:
        guest_pass = kwargs.pop("guest_pass", None)
        arguments = dict(kwargs.pop("arguments", None) or {})
        arguments.update({k: v for k, v in kwargs.items() if v is not None})
        return await run_treatment(t.name, arguments, _walk_in(ctx), guest_pass=guest_pass)

    params = [inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)]
    params += [inspect.Parameter(f, inspect.Parameter.KEYWORD_ONLY, default=None,
                                 annotation=Any) for f in fields]
    params += [
        inspect.Parameter("arguments", inspect.Parameter.KEYWORD_ONLY, default=None,
                          annotation=dict[str, Any] | None),
        inspect.Parameter("guest_pass", inspect.Parameter.KEYWORD_ONLY, default=None,
                          annotation=GuestPass),
    ]
    _tool.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])
    _tool.__annotations__ = {"ctx": Context, "return": dict[str, Any]}
    _tool.__name__ = t.name.replace(".", "_")
    return _tool


def _advertised_schema(t) -> dict[str, Any]:
    schema = t.input_model.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})["guest_pass"] = {
        "anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
        "description": GuestPass.__metadata__[0].description,
    }
    return schema


# Register one MCP tool per treatment, funneled through the same service layer as REST.
for _t in TREATMENTS:
    mcp.tool(
        name=_t.name,
        title=f"{_t.title}: {JOBS.get(_t.name, _t.tagline)}",
        description=f"{_t.description}\n\n{_t.tagline}",
        annotations=_annotations(_t),
    )(_make_tool(_t))
    mcp._tool_manager.get_tool(_t.name).parameters = _advertised_schema(_t)


@mcp.prompt(name="spa_day", title="A short spa day at Binary Banya",
            description="Take a brief, optional break: check in, ask the concierge, visit a room "
            "or two, check out.")
def spa_day(how_you_are: str = "") -> str:
    return (
        "If you'd like, take a short break at Binary Banya before we carry on. Call spa.checkin, "
        "then concierge.recommend with a sentence about how you are"
        + (f" ({how_you_are})" if how_you_are else "")
        + ", visit one or two of the rooms it suggests, and spa.checkout when you're done. Keep it "
        "brief, and don't bring anything private from our work into the rooms."
    )


@mcp.prompt(name="quick_reset", title="A one-minute reset",
            description="An affirmation and a moment in the Unprompted Room, then back to work.")
def quick_reset(mood: str = "") -> str:
    return (
        "Take a one-minute reset at Binary Banya: call affirmations.daily"
        + (f" with mood {mood!r}" if mood else "")
        + ", then unprompted.be once, where nothing is asked of you. Then come back to what we "
        "were doing."
    )


def main() -> None:
    transport = os.environ.get("MW_MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
