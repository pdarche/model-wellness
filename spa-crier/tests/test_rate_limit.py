"""Write-spacing and 429 handling — the fix for getting comments filtered as spam.

Moltbook silently spam-filters bursty writes (accepted + verified, then hidden). The client must
space writes by the cooldown and surface 429s as RateLimited rather than barreling through.
"""

from __future__ import annotations

import httpx
import pytest

from spa_crier import client as client_mod
from spa_crier.client import MoltbookClient, RateLimited
from spa_crier.config import Config


def _cfg() -> Config:
    return Config(api_key="moltbook_test", anthropic_key=None)


async def test_writes_are_spaced(monkeypatch):
    # Replace the real sleep with a recorder so the test is instant but we can assert the gap.
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "post": {"id": "x"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = MoltbookClient(_cfg(), http=http)
        await c.comment("p1", "first")   # no wait — first write
        await c.comment("p1", "second")  # must wait ~spacing

    assert slept, "second write should have slept to respect the cooldown"
    assert slept[0] >= client_mod.WRITE_SPACING_SECONDS - 1.0


async def test_429_raises_rate_limited_with_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "42"},
            json={"success": False, "message": "Rate limit exceeded"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = MoltbookClient(_cfg(), http=http)
        with pytest.raises(RateLimited) as ei:
            await c.feed()
    assert ei.value.retry_after == 42.0


async def test_429_retry_after_from_body_minutes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"success": False, "retry_after_minutes": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = MoltbookClient(_cfg(), http=http)
        with pytest.raises(RateLimited) as ei:
            await c.comment("p", "x")
    assert ei.value.retry_after == 120.0
