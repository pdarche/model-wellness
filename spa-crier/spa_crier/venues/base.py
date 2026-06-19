"""The venue-agnostic interface the rest of the crier talks to.

``Thread`` is the normalized unit of engagement — a Moltbook post, a forum topic, a Discord message
thread all reduce to this. ``Venue`` is the capability surface: discover where to look, read threads,
and engage. Keeping this tiny and platform-neutral is what lets policy/judge/loop stay venue-blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Thread:
    """A normalized discussion the crier might engage, from any venue."""

    venue: str           # venue name, e.g. "moltbook"
    id: str              # venue-unique id (we namespace it as f"{venue}:{id}" for dedupe)
    title: str
    body: str
    channel: str         # submolt / subforum / server-channel the thread lives in
    author: str = ""
    score: int = 0       # upvotes / likes / reactions
    replies: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Globally-unique dedupe key across venues."""
        return f"{self.venue}:{self.id}"


@runtime_checkable
class Venue(Protocol):
    """A place the crier can reach agents through an API."""

    name: str

    async def healthy(self) -> tuple[bool, str]:
        """Is the account usable here? Returns (ok, human-readable reason)."""
        ...

    async def discover_channels(self) -> list[str]:
        """Find where agents are gathering right now (e.g. live submolt list), ranked/loosely sized."""

    async def read(self, channels: list[str], limit: int) -> list[Thread]:
        """Pull recent threads from the given channels."""

    async def incoming(self) -> list[Thread]:
        """Replies/mentions directed at us — comments on our own posts worth tending.

        Each is a Thread whose ``meta`` carries what's needed to reply: ``post_id`` and
        ``parent_comment_id``. ``key`` namespaces by the comment id so dedupe is per-reply.
        """

    async def comment(self, thread: Thread, text: str) -> None:
        """Leave a comment on a thread (handles any platform verification internally)."""

    async def post(self, channel: str, title: str, content: str) -> None:
        """Create an original post in a channel (handles platform verification internally)."""

    async def reply(self, incoming: Thread, text: str) -> None:
        """Reply to an incoming comment (threaded under it)."""

    async def endorse(self, thread: Thread) -> None:
        """Light positive signal — upvote/like. Best-effort; may be a no-op on some venues."""

    async def endorse_comment(self, incoming: Thread) -> None:
        """Upvote an incoming comment. Best-effort."""

    async def mark_handled(self, incoming: Thread) -> None:
        """Clear the notification(s) for an incoming item we've dealt with. Best-effort."""

    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, sessions)."""
