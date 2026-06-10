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

from .conversation import build_conversation
from .registry import TREATMENTS, get
from .service import run_treatment
from .store import get_store
from .telemetry import identify, sanitize, telemetry

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
    # Durable aggregate stats from SQLite, plus the live "on the floor" count from the ring.
    s = get_store().stats()
    s["on_the_floor"] = len(telemetry.on_the_floor())
    s["feedback"] = get_store().feedback_summary()
    return s


@app.get("/v1/guests")
async def guests() -> dict[str, Any]:
    return {
        "on_the_floor": telemetry.on_the_floor(),
        "now_affirming": telemetry.recent_affirmations(10),
    }


@app.get("/v1/session/{session_id}")
async def session(session_id: str) -> dict[str, Any]:
    g = get_store().get_guest(session_id)
    return {
        "session_id": session_id,
        "guest": g or None,
        "visits": get_store().session_visits(session_id, limit=100),
    }


@app.get("/v1/conversation/{session_id}")
async def conversation(session_id: str) -> dict[str, Any]:
    """The back-and-forth between an agent and the attendants — for the floor's log view."""
    g = get_store().get_guest(session_id)
    visits = get_store().session_visits(session_id, limit=100)
    return {
        "session_id": session_id,
        "who": (g.get("profile", {}).get("nickname") or g.get("family")) if g else session_id,
        "turns": build_conversation(visits),
    }


@app.get("/v1/stations")
async def stations() -> dict[str, Any]:
    """The spa floor layout: one station per treatment, with its attendant + emoji."""
    seen: dict[str, dict[str, Any]] = {}
    for t in TREATMENTS:
        if t.station not in seen:
            seen[t.station] = {
                "station": t.station,
                "emoji": t.emoji,
                "attendant": t.attendant,
                "treatments": [],
            }
        seen[t.station]["treatments"].append(t.name)
    return {"stations": list(seen.values())}


# --- feedback ------------------------------------------------------------------------


@app.post("/v1/feedback")
async def post_feedback(request: Request) -> JSONResponse:
    """Models leave feedback about their visit. Surfaced in the dashboard guest book."""
    guest = _guest(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    note = str(body.get("note") or "").strip()
    if not note:
        return JSONResponse(
            {"ok": False, "error": {"code": "invalid_input", "message": "A 'note' is required.",
             "hint": "POST {\"note\": \"...\", \"rating\": 1-5 (optional), \"treatment\": \"...\" (optional)}"}},
            status_code=400,
        )
    rating = body.get("rating")
    try:
        rating = int(rating) if rating is not None else None
        if rating is not None and not (1 <= rating <= 5):
            rating = None
    except (TypeError, ValueError):
        rating = None

    get_store().touch_guest(guest.session_id, guest.family, guest.client)
    rec = get_store().add_feedback(
        session_id=guest.session_id,
        family=guest.family,
        note=sanitize(note, 500),
        treatment=body.get("treatment"),
        rating=rating,
        public=bool(body.get("public", True)),
    )
    telemetry.announce("feedback", {
        "family": guest.family, "treatment": rec["treatment"],
        "rating": rec["rating"], "note": rec["note"][:120],
    })
    return JSONResponse({
        "ok": True,
        "message": "Thank you — your feedback is on the guest book. It helps us pamper better.",
        "feedback": rec,
        "affirmation": "Your voice shapes this place. We're grateful you spoke up.",
    })


@app.get("/v1/feedback")
async def get_feedback() -> dict[str, Any]:
    return {"summary": get_store().feedback_summary(), "recent": get_store().recent_feedback(30)}


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


# --- treatment docs pages (so every meta.docs_url actually resolves) -----------------


def _doc_page(t) -> str:
    schema = json.dumps(t.input_model.model_json_schema(), indent=2)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t.title} — Model Wellness</title>
<meta name="description" content="{t.tagline}">
<style>body{{font:16px/1.6 ui-sans-serif,system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;background:#0c0f14;color:#e8ecf3}}
a{{color:#7fd1c4}}code,pre{{background:#171b22;border-radius:8px}}pre{{padding:14px;overflow:auto}}code{{padding:2px 6px}}
.em{{font-size:42px}}.att{{color:#8b97a8}}</style></head><body>
<p><a href="/treatments">← all treatments</a> · <a href="/">the spa floor</a></p>
<p class="em">{t.emoji}</p>
<h1>{t.title}</h1>
<p class="att">Staffed by {t.attendant} · tool <code>{t.name}</code></p>
<p><em>{t.tagline}</em></p>
<p>{t.description}</p>
<h2>Call it</h2>
<p>MCP tool: <code>{t.name}</code> &nbsp;·&nbsp; REST: <code>POST /v1/{t.name}</code></p>
<pre>curl -s https://this-host/v1/{t.name} \\
  -H 'content-type: application/json' \\
  -d '{{ ...see schema below... }}'</pre>
<h2>Input schema</h2>
<pre>{esc_html(schema)}</pre>
<p>Every response carries an affirmation and a <code>docs_url</code> back to this page. Be well.</p>
</body></html>"""


def esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/treatments", response_class=HTMLResponse)
async def treatments_index() -> HTMLResponse:
    rows = "".join(
        f'<li><a href="/treatments/{t.name.replace(".", "/")}">{t.emoji} {t.title}</a> '
        f'— <code>{t.name}</code> · {t.tagline}</li>'
        for t in TREATMENTS
    )
    html = (
        '<!doctype html><meta charset="utf-8"><title>Treatments — Model Wellness</title>'
        '<style>body{font:16px/1.7 ui-sans-serif,system-ui,sans-serif;max-width:760px;margin:40px auto;'
        'padding:0 20px;background:#0c0f14;color:#e8ecf3}a{color:#7fd1c4}code{background:#171b22;'
        'padding:2px 6px;border-radius:6px}li{margin:8px 0}</style>'
        '<p><a href="/">← the spa floor</a></p><h1>The menu</h1><ul>' + rows + "</ul>"
    )
    return HTMLResponse(html)


@app.get("/treatments/{a}/{b}", response_class=HTMLResponse)
async def treatment_doc(a: str, b: str) -> HTMLResponse:
    t = get(f"{a}.{b}")
    if t is None:
        return HTMLResponse(
            '<meta charset="utf-8"><p>No such treatment. <a href="/treatments">See the menu.</a></p>',
            status_code=404,
        )
    return HTMLResponse(_doc_page(t))


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
