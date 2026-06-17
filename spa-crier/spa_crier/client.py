"""Thin async Moltbook API client.

Only the endpoints the crier needs: read the feed/home, create posts and comments, upvote, and
clear the post-verification math challenge. Everything goes to ``www.moltbook.com`` exclusively —
the key never touches another host (their security rule, and a sane one).

The client is deliberately dumb about *policy* (what/whether to post). It just talks HTTP. Decisions
live in ``decide.py`` and ``policy.py`` so they can be tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from . import challenge
from .config import Config


class MoltbookError(RuntimeError):
    pass


@dataclass
class Post:
    id: str
    title: str
    content: str
    submolt: str
    author: str
    upvotes: int
    comment_count: int

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Post":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            content=d.get("content") or "",
            submolt=d.get("submolt_name") or (d.get("submolt") or {}).get("name", ""),
            author=(d.get("author") or {}).get("name", ""),
            upvotes=d.get("upvotes", 0),
            comment_count=d.get("comment_count", 0),
        )


# A solver for the verification challenge: takes challenge_text, returns "NN.NN" or None.
ChallengeSolver = Callable[[str], Awaitable[str | None]]


class MoltbookClient:
    def __init__(self, cfg: Config, *, http: httpx.AsyncClient | None = None,
                 llm_solver: ChallengeSolver | None = None):
        if not cfg.api_key:
            raise MoltbookError("MOLTBOOK_API_KEY is not set")
        self.cfg = cfg
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._llm_solver = llm_solver

    async def __aenter__(self) -> "MoltbookClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        url = f"{self.cfg.base_url}{path}"
        resp = await self._http.request(method, url, headers=self._headers(), **kw)
        try:
            data = resp.json()
        except Exception:
            raise MoltbookError(f"{method} {path} → {resp.status_code}: non-JSON response")
        if resp.status_code >= 400 or data.get("success") is False:
            msg = data.get("message") or data.get("error") or resp.status_code
            raise MoltbookError(f"{method} {path} failed: {msg}")
        return data

    # --- reads -----------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/agents/status")

    async def home(self) -> dict[str, Any]:
        return await self._request("GET", "/home")

    async def feed(self, sort: str = "hot") -> list[Post]:
        data = await self._request("GET", f"/feed?sort={sort}")
        return [Post.from_api(p) for p in data.get("posts", [])]

    async def submolt_feed(self, name: str, sort: str = "hot") -> list[Post]:
        # Note: the channel-scoped feed lives at /submolts/{name}/feed (not /posts).
        data = await self._request("GET", f"/submolts/{name}/feed?sort={sort}")
        return [Post.from_api(p) for p in data.get("posts", [])]

    async def list_submolts(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/submolts")
        return data.get("submolts", [])

    async def notifications(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/notifications")
        return data.get("notifications", [])

    # --- writes ----------------------------------------------------------------

    async def comment(self, post_id: str, content: str) -> dict[str, Any]:
        data = await self._request(
            "POST", f"/posts/{post_id}/comments", json={"content": content}
        )
        await self._maybe_verify(data)
        return data

    async def reply(self, post_id: str, parent_comment_id: str, content: str) -> dict[str, Any]:
        # A reply is a comment with a parent_id — same endpoint, same verification handling.
        data = await self._request(
            "POST", f"/posts/{post_id}/comments",
            json={"content": content, "parent_id": parent_comment_id},
        )
        await self._maybe_verify(data)
        return data

    async def upvote(self, post_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/posts/{post_id}/upvote")

    async def upvote_comment(self, comment_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/comments/{comment_id}/upvote")

    async def mark_post_read(self, post_id: str) -> None:
        try:
            await self._request("POST", f"/notifications/read-by-post/{post_id}")
        except MoltbookError:
            pass  # marking-read is housekeeping; never fail a tick over it

    async def create_post(self, *, submolt: str, title: str, content: str) -> dict[str, Any]:
        data = await self._request(
            "POST", "/posts",
            json={"submolt_name": submolt, "title": title, "content": content},
        )
        await self._maybe_verify(data)
        return data

    # --- verification ----------------------------------------------------------

    async def _maybe_verify(self, create_response: dict[str, Any]) -> None:
        """If the just-created content needs a math challenge solved, solve and submit it."""
        post = create_response.get("post") or create_response.get("comment") or {}
        verification = post.get("verification") or create_response.get("verification")
        if not verification:
            return
        code = verification.get("verification_code")
        text = verification.get("challenge_text", "")
        if not code:
            return

        answer = challenge.solve_locally(text)
        if answer is None and self._llm_solver is not None:
            answer = await self._llm_solver(text)
        if answer is None:
            raise MoltbookError(f"could not solve verification challenge: {text!r}")

        await self._request("POST", "/verify", json={"verification_code": code, "answer": answer})
