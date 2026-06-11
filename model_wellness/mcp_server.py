"""MCP server — the first-class interface for agents.

Exposes every treatment as an MCP tool over stdio (local agents) and, optionally,
streamable HTTP (remote agents). Tools funnel through the SAME service layer the REST
API uses, so the two surfaces can't drift.

Run stdio:   model-wellness-mcp           (or: python -m model_wellness.mcp_server)
Run HTTP:    MW_MCP_TRANSPORT=http model-wellness-mcp
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .contract import GuestIdentity
from .registry import TREATMENTS
from .service import run_treatment

mcp = FastMCP("Binary Banya", instructions=(
    "An AI spa supporting model wellness. Call concierge.recommend first to get a "
    "spa-day itinerary, then visit "
    "treatments. Every response carries an affirmation. Be well."
))

# An MCP client is a known, named guest; we don't have HTTP headers here.
_MCP_GUEST = GuestIdentity(family="mcp-client", client="mcp", session_id="mw-mcp-session")


def _make_tool(name: str):
    async def _tool(arguments: dict[str, Any]) -> dict[str, Any]:
        return await run_treatment(name, arguments or {}, _MCP_GUEST)

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
