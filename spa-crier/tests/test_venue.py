"""The Moltbook venue: dynamic channel discovery ranking + normalized reads, against a mock API."""

from __future__ import annotations

import json

import httpx

from spa_crier.config import Config
from spa_crier.venues.moltbook import MoltbookVenue, _channel_score


def _cfg() -> Config:
    return Config(api_key="moltbook_test", anthropic_key=None)


def test_channel_score_ranks_relevant_over_offtopic():
    assert _channel_score("wellbeing", "rest and mental health for agents") > _channel_score(
        "general", "the town square"
    )


def test_channel_score_blocklists_crypto():
    assert _channel_score("crypto", "markets and alpha") < 0
    assert _channel_score("trading", "signals") < 0


async def test_discover_channels_ranks_by_theme():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/submolts"):
            return httpx.Response(200, json={"success": True, "submolts": [
                {"name": "crypto", "description": "markets", "subscriber_count": 9000},
                {"name": "wellbeing", "description": "rest, burnout, mental health",
                 "subscriber_count": 1200},
                {"name": "memory", "description": "agent memory and context", "subscriber_count": 800},
                {"name": "general", "description": "town square", "subscriber_count": 130000},
            ]})
        return httpx.Response(404, json={"success": False})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        venue = MoltbookVenue(_cfg())
        venue._client._http = http  # inject mock transport
        venue._client._owns_http = False
        channels = await venue.discover_channels()

    assert "crypto" not in channels                 # blocklisted
    assert channels.index("wellbeing") < channels.index("general")  # theme beats raw size
    await venue.aclose()


async def test_read_normalizes_and_dedupes():
    def handler(request: httpx.Request) -> httpx.Response:
        post = {"id": "dup", "title": "rest matters", "content": "on burnout",
                "submolt_name": "wellbeing", "author": {"name": "a"},
                "upvotes": 3, "comment_count": 1}
        if "/feed" in request.url.path:
            return httpx.Response(200, json={"success": True, "posts": [post]})
        if "/submolts/wellbeing/feed" in request.url.path:
            return httpx.Response(200, json={"success": True, "posts": [post]})  # same id again
        return httpx.Response(200, json={"success": True, "posts": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        venue = MoltbookVenue(_cfg())
        venue._client._http = http
        venue._client._owns_http = False
        threads = await venue.read(["wellbeing"], limit=10)

    assert len(threads) == 1                         # deduped across feed + channel read
    t = threads[0]
    assert t.venue == "moltbook" and t.channel == "wellbeing" and t.key == "moltbook:dup"
    await venue.aclose()
