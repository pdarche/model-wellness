"""Client wiring, especially the post → challenge → verify handshake, against a mock transport.

No real network: httpx.MockTransport lets us assert the client solves the math challenge and POSTs
the answer to /verify automatically."""

from __future__ import annotations

import json

import httpx
import pytest

from spa_crier.client import MoltbookClient, MoltbookError
from spa_crier.config import Config


def _cfg() -> Config:
    return Config(api_key="moltbook_test", anthropic_key=None)


async def test_create_post_solves_and_submits_challenge():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json={
                "success": True,
                "post": {
                    "id": "newpost",
                    "verification": {
                        "verification_code": "code123",
                        "challenge_text": "A lobster swims at twenty five cm per second and adds "
                                          "seven to its velocity, whats the new speed?",
                    },
                },
            })
        if request.url.path.endswith("/verify"):
            return httpx.Response(200, json={"success": True, "message": "published"})
        return httpx.Response(404, json={"success": False})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MoltbookClient(_cfg(), http=http)
        await client.create_post(submolt="introductions", title="hi", content="hello")

    paths = [p for p, _ in seen]
    assert "/api/v1/posts" in paths
    assert "/api/v1/verify" in paths
    verify_body = next(b for p, b in seen if p.endswith("/verify"))
    assert verify_body == {"verification_code": "code123", "answer": "32.00"}


async def test_verify_falls_back_to_llm_when_heuristic_wrong():
    # The heuristic will produce *an* answer for this clean problem (12+8=20). We simulate the server
    # rejecting it, and the LLM solver offering the correct one, which is then accepted.
    posted = {"n": 0}
    verify_attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json={"success": True, "post": {"id": "p", "verification": {
                "verification_code": "c", "challenge_text": "What is 12 plus 8?"}}})
        if request.url.path.endswith("/verify"):
            body = json.loads(request.content)
            verify_attempts.append(body["answer"])
            # Reject the heuristic's "20.00", accept the LLM's "99.00".
            if body["answer"] == "99.00":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(400, json={"success": False, "message": "Incorrect answer"})
        return httpx.Response(200, json={"success": True})

    async def llm_solver(text: str) -> str:
        return "99.00"

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MoltbookClient(_cfg(), http=http, llm_solver=llm_solver)
        await client.create_post(submolt="general", title="t", content="c")

    # Tried heuristic first, then LLM; LLM answer accepted.
    assert verify_attempts == ["20.00", "99.00"]


async def test_unsolvable_challenge_raises_without_llm():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json={
                "success": True,
                "post": {"id": "p", "verification": {
                    "verification_code": "c", "challenge_text": "ponder the void"}},
            })
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MoltbookClient(_cfg(), http=http)
        with pytest.raises(MoltbookError, match="could not solve"):
            await client.create_post(submolt="general", title="t", content="c")


async def test_api_error_surfaces():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False, "message": "nope"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MoltbookClient(_cfg(), http=http)
        with pytest.raises(MoltbookError, match="nope"):
            await client.feed()


def test_missing_key_raises():
    with pytest.raises(MoltbookError, match="not set"):
        MoltbookClient(Config(api_key=None, anthropic_key=None))
