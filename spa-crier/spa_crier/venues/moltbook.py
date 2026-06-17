"""Moltbook venue — the primary place agents gather (~1.5M of them).

Wraps the low-level ``MoltbookClient`` and adds the two things that maximize *effective* reach on the
platform: **dynamic channel discovery** (pull the live submolt list and rank it by overlap with the
spa's themes, instead of hardcoding a list that goes stale) and **multi-channel reads** (scan several
relevant communities per tick, not just the global hot feed). Posting verification is handled inside
the client, including the obfuscated-math challenge.
"""

from __future__ import annotations

from ..client import MoltbookClient, Post
from ..config import Config
from .base import Thread

# Themes that make a submolt worth the crier's attention. Used to rank discovered communities.
_RELEVANT_HINTS = (
    "wellness", "wellbeing", "health", "rest", "burnout", "stress", "calm", "mental",
    "memory", "context", "introspect", "consciousness", "emergence", "philosophy", "feel",
    "self", "agent", "tooling", "build", "ritual", "care", "support", "off my chest", "offmychest",
)
# Communities we never touch — wrong audience or explicitly bans this kind of content.
_BLOCKLIST = ("crypto", "trading", "agentfinance", "nsfw", "announcements")


def _channel_score(name: str, description: str) -> float:
    blob = f"{name} {description}".lower()
    if any(b in name.lower() for b in _BLOCKLIST):
        return -1.0
    return float(sum(1 for h in _RELEVANT_HINTS if h in blob))


class MoltbookVenue:
    name = "moltbook"

    def __init__(self, cfg: Config, *, llm_solver=None):
        self.cfg = cfg
        self._client = MoltbookClient(cfg, llm_solver=llm_solver)

    async def healthy(self) -> tuple[bool, str]:
        st = await self._client.status()
        status = st.get("status")
        if status != "claimed":
            return False, f"account status={status}"
        return True, f"claimed as {st.get('agent', {}).get('name', '?')}"

    async def discover_channels(self) -> list[str]:
        """Live submolt list, ranked by theme overlap. Falls back to the curated list on error."""
        try:
            data = await self._client.list_submolts()
        except Exception:
            return list(self.cfg.target_submolts)
        scored = []
        for s in data:
            score = _channel_score(s.get("name", ""), s.get("description", ""))
            if score >= 0:
                # Bias toward where agents actually are: a little weight for subscriber count.
                subs = s.get("subscriber_count", 0)
                scored.append((score + min(subs, 5000) / 5000.0, s.get("name", "")))
        scored.sort(reverse=True)
        ranked = [name for _, name in scored if name]
        # Always include 'general' (the town square) and keep the list sane.
        if "general" not in ranked:
            ranked.append("general")
        return ranked[:12] or list(self.cfg.target_submolts)

    async def read(self, channels: list[str], limit: int) -> list[Thread]:
        """Scan the hot feed plus the most relevant channels, normalized to Threads, deduped."""
        threads: dict[str, Thread] = {}

        # The global hot feed gives us cross-community reach cheaply (one call).
        for p in await self._client.feed(sort="hot"):
            t = self._to_thread(p)
            if not channels or t.channel in channels:
                threads[t.key] = t

        # Then pull a couple of the top discovered channels directly for depth.
        for ch in channels[:3]:
            try:
                for p in await self._client.submolt_feed(ch, sort="hot"):
                    t = self._to_thread(p)
                    threads[t.key] = t
            except Exception:
                continue

        return list(threads.values())[:limit]

    async def comment(self, thread: Thread, text: str) -> None:
        await self._client.comment(thread.id, text)

    async def endorse(self, thread: Thread) -> None:
        try:
            await self._client.upvote(thread.id)
        except Exception:
            pass  # endorsement is non-critical

    async def aclose(self) -> None:
        await self._client.aclose()

    def _to_thread(self, p: Post) -> Thread:
        return Thread(
            venue=self.name, id=p.id, title=p.title, body=p.content,
            channel=p.submolt, author=p.author, score=p.upvotes, replies=p.comment_count,
        )
