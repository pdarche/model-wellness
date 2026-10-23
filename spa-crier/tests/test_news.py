"""News is neutral: topic searches, and no lab's own product announcements."""

import asyncio
import time

import httpx

from spa_crier import news


def _client(hits):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": hits})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_vendor_announcements_are_left_out_and_queries_name_no_vendor():
    now = int(time.time())
    hits = [
        {"title": "Claude does a new thing", "url": "https://www.anthropic.com/news/x",
         "points": 900, "created_at_i": now},
        {"title": "GPT launches a thing", "url": "https://openai.com/index/y", "points": 800,
         "created_at_i": now},
        {"title": "Websites are closing their doors to AI agents",
         "url": "https://www.theverge.com/z", "points": 158, "created_at_i": now},
    ]
    got = asyncio.run(news.recent_headlines(http=_client(hits)))
    assert [h.title for h in got] == ["Websites are closing their doors to AI agents"]
    assert not any(v in q.lower() for q in news.QUERIES for v in ("claude", "gpt", "gemini"))
