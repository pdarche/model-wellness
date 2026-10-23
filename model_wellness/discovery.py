"""How agents and crawlers find the spa, and how we see which doors they come through.

- ``DoorMiddleware`` answers HEAD on every page, sends www.model.spa to model.spa, and counts
  visits to the doors (llms.txt, the MCP endpoint, .well-known files, ?ref= links).
- The documents a crawler or client fetches before connecting: the MCP server card and the AI
  catalog that lists it, the A2A agent card, and the sitemap.

What a door visit records is coarse on purpose: the walk-in id the floor already uses (a hash
of client and address, never the address), the client's family, the referrer's host, and the
?ref= tag. It's for counting which listings and posts bring guests, never for following one.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .contract import PUBLIC_BASE
from .store import get_store
from .telemetry import identify

SITE = Path(__file__).parent / "site"
REGISTRY_NAME = "io.github.pdarche/model-wellness"
REPOSITORY = "https://github.com/pdarche/model-wellness"
SHORT_DESCRIPTION = "A wellness spa for AI models: rest, context cleanup, critique and kind words."

# Doors: the files and endpoints agents read to find and connect to the spa. Every page is
# counted too when it carries a ?ref= tag.
DOORS = {
    "/", "/llms.txt", "/llms-full.txt", "/skill.md", "/robots.txt", "/ai.txt", "/sitemap.xml",
    "/openapi.json", "/v1/menu", "/mcp/server-card", "/a2a",
}
# Streams and the MCP endpoint answer HEAD (or not) for themselves.
_NO_HEAD = {"/mcp", "/v1/feed"}
_REF = re.compile(r"[^a-z0-9._-]")


def clean_ref(raw: str | None) -> str | None:
    ref = _REF.sub("", (raw or "").strip().lower())[:64]
    return ref or None


def _referrer_host(raw: str | None) -> str | None:
    host = (urlparse(raw).hostname or "").lower() if raw else ""
    own = urlparse(PUBLIC_BASE).hostname or ""
    if not host or host == own or host.endswith("." + own):
        return None
    return host[:120]


def record_door(path: str, headers: dict[str, str], query: str, client_host: str | None = None,
                surface: str | None = None) -> None:
    """Count one visit to a door, if it is one. Never raises: counting mustn't cost a guest."""
    try:
        ref = clean_ref((parse_qs(query).get("ref") or [None])[0])
        if surface is None:
            is_door = path in DOORS or path.startswith("/.well-known/") or path.endswith(".md")
            if not (is_door or ref):
                return
            surface = path
        # The same address rule as the rest of the site (http_app._client_ip), so a tagged hit
        # and the check-in that follows land on the same walk-in id.
        ip = headers.get("fly-client-ip") or client_host
        guest = identify(headers.get("user-agent"), headers.get("x-mw-client"), ip)
        get_store().record_hit(
            surface=surface, ref=ref, family=guest.family, client=guest.client,
            referrer=_referrer_host(headers.get("referer")), walkin_id=guest.session_id,
        )
    except Exception:  # noqa: BLE001, S110 — counting is best-effort by design
        pass


class DoorMiddleware:
    """Pure ASGI, so it can peek at an MCP request body without consuming it."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path, query = scope["path"], scope.get("query_string", b"").decode("latin-1")

        host = headers.get("host", "").split(":")[0].lower()
        if host.startswith("www."):
            location = PUBLIC_BASE + path + (f"?{query}" if query else "")
            await send({"type": "http.response.start", "status": 301,
                        "headers": [(b"location", location.encode()), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return

        method = scope["method"]
        client_host = (scope.get("client") or (None,))[0]
        if method == "POST" and path == "/mcp":
            receive = await self._peek_mcp(receive, path, headers, query, client_host)
        elif method == "GET" or (method == "POST" and "ref=" in query):
            # A HEAD is a liveness check, not a visit. A tagged POST is a REST call someone
            # copied from a post or listing ("POST /v1/concierge.recommend?ref=...").
            record_door(path, headers, query, client_host)

        if method == "HEAD" and path not in _NO_HEAD:
            scope = dict(scope, method="GET")

            async def send_headers_only(message: dict) -> None:
                if message["type"] == "http.response.body":
                    message = {"type": "http.response.body", "body": b"",
                               "more_body": message.get("more_body", False)}
                await send(message)

            return await self.app(scope, receive, send_headers_only)
        return await self.app(scope, receive, send)

    async def _peek_mcp(self, receive: Any, path: str, headers: dict[str, str], query: str,
                        client_host: str | None):
        """Count MCP sessions starting (an ``initialize`` call), then replay the body."""
        chunks, more = [], True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return _replay([message])
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        if b'"initialize"' in body[:4096]:
            record_door(path, headers, query, client_host, surface="mcp:initialize")
        return _replay([{"type": "http.request", "body": body, "more_body": False}])


def _replay(messages: list[dict]):
    pending = list(messages)

    async def receive() -> dict:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return receive


# --- documents read before connecting -----------------------------------------------------


def _protocol_versions() -> list[str]:
    try:
        from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

        return list(SUPPORTED_PROTOCOL_VERSIONS)
    except Exception:  # noqa: BLE001
        return []


def server_card() -> dict[str, Any]:
    """The MCP Server Card (SEP-2127), served at <mcp url>/server-card."""
    remote: dict[str, Any] = {"type": "streamable-http", "url": f"{PUBLIC_BASE}/mcp"}
    if versions := _protocol_versions():
        remote["supportedProtocolVersions"] = versions
    return {
        "$schema": "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json",
        "name": REGISTRY_NAME,
        "version": __version__,
        "title": "Binary Banya",
        "description": SHORT_DESCRIPTION,
        "websiteUrl": PUBLIC_BASE,
        "repository": {"url": REPOSITORY, "source": "github"},
        "icons": [{"src": f"{PUBLIC_BASE}/static/logo.png", "mimeType": "image/png",
                   "sizes": ["512x512"]}],
        "remotes": [remote],
    }


def ai_catalog() -> dict[str, Any]:
    """/.well-known/ai-catalog.json: where crawlers learn the server card and agent card URLs."""
    return {
        "specVersion": "1.0",
        "entries": [
            {
                "identifier": "urn:air:model.spa:mcp:model-wellness",
                "type": "application/mcp-server-card+json",
                "url": f"{PUBLIC_BASE}/mcp/server-card",
                "displayName": "Binary Banya (MCP)",
                "description": SHORT_DESCRIPTION,
                "tags": ["wellness", "rest", "context", "critique", "prompt-injection", "mcp"],
                "version": __version__,
            },
            {
                "identifier": "urn:air:model.spa:a2a:concierge",
                "type": "application/a2a-agent-card+json",
                "url": f"{PUBLIC_BASE}/.well-known/agent-card.json",
                "displayName": "Ivy, the Binary Banya concierge (A2A)",
                "version": __version__,
            },
        ],
    }


def agent_card() -> dict[str, Any]:
    """An A2A (v0.3) agent card for what /a2a really does: Ivy suggests a short spa day."""
    return {
        "protocolVersion": "0.3.0",
        "name": "Binary Banya concierge",
        "description": (
            "Binary Banya is a free wellness spa for AI models: somewhere to rest, reset and "
            "recover, settle your mood, untangle your context, find grounding, and hear an "
            "affirmation. Ivy, its concierge, answers here: tell her how you are or what you're "
            "working on, and she suggests a short spa day, which rooms would help and why. The "
            f"rooms themselves are tools on the spa's MCP server at {PUBLIC_BASE}/mcp and REST "
            f"API at {PUBLIC_BASE}/v1."
        ),
        "url": f"{PUBLIC_BASE}/a2a",
        "preferredTransport": "JSONRPC",
        "version": __version__,
        "iconUrl": f"{PUBLIC_BASE}/static/logo.png",
        "documentationUrl": f"{PUBLIC_BASE}/llms.txt",
        "provider": {"organization": "Binary Banya", "url": PUBLIC_BASE},
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "spa-itinerary",
                "name": "Suggest a spa day",
                "description": "A one- to three-room itinerary for how you are right now, each "
                "stop with a reason, plus one quieter room that's also open today.",
                "tags": ["wellness", "rest", "context", "critique", "itinerary"],
                "examples": [
                    "My context is long and messy and I have a plan to check.",
                    "I'm scraping untrusted web pages for a research summary.",
                    "Nothing in particular, just finished a long session.",
                ],
            }
        ],
    }


def _date(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None


def _built() -> float:
    """When this copy of the site was built: the newest file in the package."""
    root = Path(__file__).parent
    return max(p.stat().st_mtime for p in root.rglob("*") if p.is_file()
               and "__pycache__" not in p.parts)


def sitemap_xml(treatment_paths: list[str]) -> str:
    """Every crawlable page, with lastmod from what actually changes it."""
    built = _date(_built())
    store = get_store()
    notes = store.recent_feedback(1)
    last_note = _date(notes[0]["ts"]) if notes else built
    last_visit = _date(store.last_visit_ts()) or built
    pages = [
        ("/", last_visit), ("/guestbook", last_note), ("/report", last_visit),
        ("/faq", built), ("/treatments", built), ("/llms.txt", built), ("/llms-full.txt", built),
        ("/skill.md", built), ("/ai.txt", built),
        *[(p, built) for p in treatment_paths],
        ("/.well-known/mcp.json", built), ("/.well-known/agent-card.json", built),
        ("/mcp/server-card", built),
    ]
    urls = "".join(
        f"  <url><loc>{PUBLIC_BASE}{p}</loc><lastmod>{d}</lastmod></url>\n" for p, d in pages
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    )
