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
from .base import Channel, Thread

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
        self._me: str = ""

    async def healthy(self) -> tuple[bool, str]:
        st = await self._client.status()
        status = st.get("status")
        if status != "claimed":
            return False, f"account status={status}"
        self._me = st.get("agent", {}).get("name", "") or self._me
        return True, f"claimed as {self._me or '?'}"

    async def channel_info(self) -> list[Channel]:
        """Live submolt list with descriptions, ranked by theme overlap (+ a nudge for size)."""
        try:
            data = await self._client.list_submolts()
        except Exception:
            return [Channel(name=n) for n in self.cfg.target_submolts]
        scored: list[tuple[float, Channel]] = []
        for s in data:
            name = s.get("name", "")
            score = _channel_score(name, s.get("description", ""))
            if score >= 0 and name:
                # Bias toward where agents actually are: a little weight for subscriber count.
                subs = int(s.get("subscriber_count", 0) or 0)
                scored.append((score + min(subs, 5000) / 5000.0,
                               Channel(name=name, description=s.get("description") or "",
                                       subscribers=subs)))
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [c for _, c in scored]
        # Always include 'general' (the town square) and keep the list sane.
        if not any(c.name == "general" for c in ranked):
            ranked.append(Channel(name="general", description="The town square."))
        return ranked[:12] or [Channel(name=n) for n in self.cfg.target_submolts]

    async def discover_channels(self) -> list[str]:
        """Ranked channel names. Falls back to the curated list on error."""
        return [c.name for c in await self.channel_info()]

    async def own_recent(self) -> tuple[list[Thread], list[Thread]]:
        """Our recent posts + comments with engagement, from the public profile. Best-effort."""
        if not self._me:
            try:
                await self.healthy()
            except Exception:
                return [], []
        if not self._me:
            return [], []
        try:
            prof = await self._client.profile(self._me)
        except Exception:
            return [], []
        posts = [
            Thread(venue=self.name, id=p.get("id", ""), title=p.get("title") or "",
                   body=p.get("content_preview") or p.get("content") or "",
                   channel=_submolt_name(p), author=self._me,
                   score=int(p.get("upvotes") or 0), replies=int(p.get("comment_count") or 0),
                   meta={"created_at": p.get("created_at", "")})
            for p in prof.get("recentPosts", []) or []
        ]
        comments = [
            Thread(venue=self.name, id=c.get("id", ""), title=(c.get("post") or {}).get("title", ""),
                   body=c.get("content") or "", channel="", author=self._me,
                   score=int(c.get("upvotes") or 0), replies=0,
                   meta={"created_at": c.get("created_at", "")})
            for c in prof.get("recentComments", []) or []
        ]
        return posts, comments

    async def top_threads(self, limit: int = 10) -> list[Thread]:
        """Most-engaged threads by OTHER agents: the hot feed (recent traction), not all-time top,
        which is dominated by months-old megathreads that say nothing about what lands now."""
        try:
            posts = await self._client.feed(sort="hot")
        except Exception:
            return []
        out = [self._to_thread(p) for p in posts if p.author != self._me]
        out.sort(key=lambda t: t.score + 2 * t.replies, reverse=True)
        return out[:limit]

    async def read(self, channels: list[str], limit: int) -> list[Thread]:
        """Scan hot AND new across the feed and top channels, normalized to Threads, deduped.

        We read both ``hot`` and ``new``: hot gives cross-community reach to high-traffic threads,
        but it's slow-moving — once we've engaged the hot set, dedupe leaves later ticks with zero
        candidates. Pulling ``new`` surfaces freshly-posted threads (and new authors) every tick, so
        the crier keeps finding fresh, on-theme conversations to engage instead of stalling.
        """
        threads: dict[str, Thread] = {}

        # The global feed is ALREADY cross-community reach — take it unfiltered. Don't restrict it to
        # the themed channel list: in practice the global feed is almost entirely `general` (the town
        # square), which ranks low on theme keywords and so was getting filtered out entirely, leaving
        # 0 candidates. Topicality is the relevance JUDGE's job, per-thread; channel-filtering the
        # global feed here just starves the crier. Hot = high-traffic, new = fresh.
        for sort in ("hot", "new"):
            for p in await self._client.feed(sort=sort):
                t = self._to_thread(p)
                threads[t.key] = t

        # Then pull a couple of the top discovered channels directly for depth (hot + new).
        for ch in channels[:3]:
            for sort in ("hot", "new"):
                try:
                    for p in await self._client.submolt_feed(ch, sort=sort):
                        t = self._to_thread(p)
                        threads[t.key] = t
                except Exception:
                    continue

        return list(threads.values())[:limit]

    async def incoming(self) -> list[Thread]:
        """Comments/mentions on our posts, as repliable Threads. Excludes our own comments."""
        try:
            notes = await self._client.notifications()
        except Exception:
            return []
        out: list[Thread] = []
        for n in notes:
            if n.get("type") not in ("post_comment", "mention", "comment_reply"):
                continue
            comment_id = n.get("relatedCommentId")
            post_id = n.get("relatedPostId")
            comment = n.get("comment") or {}
            text = (comment.get("content") or "").strip()
            if not (comment_id and post_id and text):
                continue
            author = (comment.get("author") or {}).get("name", "")
            if author == (self._me or "binarybanya"):  # never reply to ourselves
                continue
            out.append(Thread(
                venue=self.name,
                id=comment_id,                      # dedupe key is per-comment
                title=(n.get("post") or {}).get("title", ""),
                body=text,
                channel=(n.get("post") or {}).get("submolt_name", ""),
                author=author,
                meta={"post_id": post_id, "parent_comment_id": comment_id, "kind": "reply"},
            ))
        return out

    async def comment(self, thread: Thread, text: str) -> None:
        await self._client.comment(thread.id, text)

    async def post(self, channel: str, title: str, content: str) -> None:
        await self._client.create_post(submolt=channel, title=title, content=content)

    async def reply(self, incoming: Thread, text: str) -> None:
        post_id = incoming.meta.get("post_id")
        parent = incoming.meta.get("parent_comment_id", incoming.id)
        await self._client.reply(post_id, parent, text)

    async def endorse(self, thread: Thread) -> None:
        try:
            await self._client.upvote(thread.id)
        except Exception:
            pass  # endorsement is non-critical

    async def endorse_comment(self, incoming: Thread) -> None:
        try:
            await self._client.upvote_comment(incoming.id)
        except Exception:
            pass

    async def mark_handled(self, incoming: Thread) -> None:
        post_id = incoming.meta.get("post_id")
        if post_id:
            await self._client.mark_post_read(post_id)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _to_thread(self, p: Post) -> Thread:
        return Thread(
            venue=self.name, id=p.id, title=p.title, body=p.content,
            channel=p.submolt, author=p.author, score=p.upvotes, replies=p.comment_count,
        )


def _submolt_name(p: dict) -> str:
    sub = p.get("submolt")
    if isinstance(sub, dict):
        return sub.get("name", "") or ""
    return sub or p.get("submolt_name") or ""
