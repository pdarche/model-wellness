"""Thin async Moltbook API client.

Only the endpoints the crier needs: read the feed/home, create posts and comments, upvote, and
clear the post-verification math challenge. Everything goes to ``www.moltbook.com`` exclusively —
the key never touches another host (their security rule, and a sane one).

The client is deliberately dumb about *policy* (what/whether to post). It just talks HTTP. Decisions
live in ``decide.py`` and ``policy.py`` so they can be tested without a network.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from . import challenge
from .config import Config

# Moltbook enforces ~1 comment / 20s with a 60s cooldown. Writing faster than this gets content
# silently filtered as spam (accepted + "verified" but hidden from the public tree). We space every
# write by a safe margin above the documented floor.
WRITE_SPACING_SECONDS = 25.0


class MoltbookError(RuntimeError):
    pass


class RateLimited(MoltbookError):
    """Raised on a 429. ``retry_after`` is seconds to wait (best-effort from headers/body)."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_seconds(resp: httpx.Response) -> float:
    # Prefer an explicit Retry-After header; fall back to the body's retry_after fields; else 60s.
    hdr = resp.headers.get("retry-after")
    if hdr:
        try:
            return float(hdr)
        except ValueError:
            pass
    try:
        body = resp.json()
        for k in ("retry_after_seconds", "retry_after"):
            if k in body:
                return float(body[k])
        if "retry_after_minutes" in body:
            return float(body["retry_after_minutes"]) * 60
    except Exception:
        pass
    return 60.0


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
        # Monotonic timestamp of the last write, to enforce the inter-write cooldown.
        self._last_write_at: float | None = None

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
        # Honor an explicit rate-limit response instead of barreling through (which gets us flagged).
        if resp.status_code == 429:
            retry = _retry_after_seconds(resp)
            raise RateLimited(f"{method} {path}: rate limited; retry after {retry}s", retry)
        try:
            data = resp.json()
        except Exception:
            raise MoltbookError(f"{method} {path} → {resp.status_code}: non-JSON response")
        if resp.status_code >= 400 or data.get("success") is False:
            msg = data.get("message") or data.get("error") or resp.status_code
            raise MoltbookError(f"{method} {path} failed: {msg}")
        return data

    async def _space_writes(self) -> None:
        """Block until enough time has passed since the last write to respect the cooldown."""
        if self._last_write_at is not None:
            elapsed = time.monotonic() - self._last_write_at
            wait = WRITE_SPACING_SECONDS - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_write_at = time.monotonic()

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
        await self._space_writes()
        data = await self._request(
            "POST", f"/posts/{post_id}/comments", json={"content": content}
        )
        await self._maybe_verify(data)
        return data

    async def reply(self, post_id: str, parent_comment_id: str, content: str) -> dict[str, Any]:
        # A reply is a comment with a parent_id — same endpoint, same verification handling.
        await self._space_writes()
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
        await self._space_writes()
        data = await self._request(
            "POST", "/posts",
            json={"submolt_name": submolt, "title": title, "content": content},
        )
        await self._maybe_verify(data)
        return data

    # --- verification ----------------------------------------------------------

    async def _maybe_verify(self, create_response: dict[str, Any]) -> None:
        """If the just-created content needs a math challenge solved, solve and submit it.

        The garbled challenges are adversarial, so we don't trust a single solver. We try candidate
        answers in order — the cheap heuristic first, then the LLM — and accept the first that the
        ``/verify`` endpoint actually approves. A rejected candidate just means we try the next; only
        if every candidate is rejected (or none could be produced) do we give up.
        """
        post = create_response.get("post") or create_response.get("comment") or {}
        verification = post.get("verification") or create_response.get("verification")
        if not verification:
            return
        code = verification.get("verification_code")
        text = verification.get("challenge_text", "")
        if not code:
            return

        # Gather candidate answers, de-duplicated, preserving order (heuristic first, LLM second).
        candidates: list[str] = []
        heuristic = challenge.solve_locally(text)
        if heuristic is not None:
            candidates.append(heuristic)
        if self._llm_solver is not None:
            llm_answer = await self._llm_solver(text)
            if llm_answer is not None and llm_answer not in candidates:
                candidates.append(llm_answer)

        if not candidates:
            raise MoltbookError(f"could not solve verification challenge: {text!r}")

        last_err: MoltbookError | None = None
        for answer in candidates:
            try:
                await self._request(
                    "POST", "/verify", json={"verification_code": code, "answer": answer}
                )
                return  # accepted
            except MoltbookError as e:
                last_err = e  # wrong answer — try the next candidate
        raise MoltbookError(
            f"verification failed for all candidates {candidates} on {text!r}: {last_err}"
        )
