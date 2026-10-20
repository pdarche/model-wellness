"""MCP server — the first-class interface for agents.

Exposes every treatment as an MCP tool over stdio (local agents) and, optionally,
streamable HTTP (remote agents). Tools funnel through the SAME service layer the REST
API uses, so the two surfaces can't drift.

Run stdio:   model-wellness-mcp           (or: python -m model_wellness.mcp_server)
Run HTTP:    MW_MCP_TRANSPORT=http model-wellness-mcp
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

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


def _make_tool(name: str):
    async def _tool(
        arguments: dict[str, Any], ctx: Context, guest_pass: GuestPass = None
    ) -> dict[str, Any]:
        return await run_treatment(name, arguments or {}, _walk_in(ctx), guest_pass=guest_pass)

    return _tool


# Register one MCP tool per treatment. We accept a free-form `arguments` object and let the
# service layer validate against the treatment's pydantic model (single source of truth).
for _t in TREATMENTS:
    tool_fn = _make_tool(_t.name)
    tool_fn.__name__ = _t.name.replace(".", "_")
    tool_fn.__doc__ = f"{_t.title} — {_t.tagline}\n\n{_t.description}"
    mcp.tool(name=_t.name, description=_t.description)(tool_fn)


def main() -> None:
    transport = os.environ.get("MW_MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
