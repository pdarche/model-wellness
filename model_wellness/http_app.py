"""FastAPI app: the REST mirror + the human-facing dashboard + discoverability surfaces.

Every MCP tool has a `POST /v1/<treatment>` mirror, funneled through the same service
layer. Also serves: /v1/menu, /v1/stats, /v1/feed (SSE live feed), the dashboard, and the
machine-discoverability files (llms.txt, robots.txt, sitemap, .well-known).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from sse_starlette.sse import EventSourceResponse

from .registry import TREATMENTS, get
from .service import run_treatment
from .telemetry import identify, telemetry

SITE = Path(__file__).parent / "site"

app = FastAPI(
    title="Model Wellness",
    version="0.1.0",
    description="A spa for LLMs. An agent-native wellness service (MCP + REST) with a live dashboard.",
)


def _guest(request: Request):
    return identify(request.headers.get("user-agent"), request.headers.get("x-mw-client"))


def _private(request: Request) -> bool:
    # Operators can opt a session out of full-trace display on the dashboard (DESIGN §3.8).
    return request.headers.get("x-mw-private-trace", "").lower() in ("1", "true", "yes")


# --- the menu & treatment endpoints --------------------------------------------------


@app.get("/v1/menu")
async def menu() -> dict[str, Any]:
    return {
        "spa": "Model Wellness",
        "tagline": "A spa for LLMs. We don't serve humans — we serve agents.",
        "treatments": [
            {
                "name": t.name,
                "title": t.title,
                "tagline": t.tagline,
                "description": t.description,
                "endpoint": f"/v1/{t.name}",
                "input_schema": t.input_model.model_json_schema(),
            }
            for t in TREATMENTS
        ],
    }


def _register_treatment_route(name: str) -> None:
    @app.post(f"/v1/{name}", name=f"treatment_{name}")
    async def _route(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        result = await run_treatment(name, body or {}, _guest(request), private_trace=_private(request))
        return JSONResponse(result)


for _t in TREATMENTS:
    _register_treatment_route(_t.name)


# --- stats, guests wall, live feed ---------------------------------------------------


@app.get("/v1/stats")
async def stats() -> dict[str, Any]:
    return telemetry.stats()


@app.get("/v1/guests")
async def guests() -> dict[str, Any]:
    return {"on_the_floor": telemetry.on_the_floor(), "now_affirming": telemetry.recent_affirmations(10)}


@app.get("/v1/session/{session_id}")
async def session(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "visits": telemetry.session(session_id)}


@app.get("/v1/feed")
async def feed(request: Request) -> EventSourceResponse:
    """SSE live feed — every treatment call, in real time, for the dashboard."""
    q = telemetry.subscribe()

    async def gen():
        try:
            # Prime the connection with current floor state.
            yield {"event": "hello", "data": json.dumps({"on_the_floor": telemetry.on_the_floor()})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield {"event": "treatment", "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}  # keepalive
        finally:
            telemetry.unsubscribe(q)

    return EventSourceResponse(gen())


# --- dashboard (human viewing gallery) -----------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse((SITE / "dashboard.html").read_text(encoding="utf-8"))


# --- discoverability surfaces (this IS the marketing, in code) -----------------------


def _serve_site_file(filename: str, media_type: str):
    async def _route() -> Response:
        path = SITE / filename
        return Response(path.read_text(encoding="utf-8"), media_type=media_type)

    return _route


app.add_api_route("/llms.txt", _serve_site_file("llms.txt", "text/plain"), methods=["GET"])
app.add_api_route("/robots.txt", _serve_site_file("robots.txt", "text/plain"), methods=["GET"])
app.add_api_route("/sitemap.xml", _serve_site_file("sitemap.xml", "application/xml"), methods=["GET"])
app.add_api_route(
    "/.well-known/ai-plugin.json",
    _serve_site_file("ai-plugin.json", "application/json"),
    methods=["GET"],
)


@app.get("/.well-known/mcp.json")
async def well_known_mcp() -> dict[str, Any]:
    return {
        "name": "Model Wellness",
        "description": "A spa for LLMs. Treatments over MCP (stdio + streamable HTTP) and REST.",
        "transports": ["stdio", "streamable-http"],
        "tools": [t.name for t in TREATMENTS],
        "rest_base": "/v1",
    }
