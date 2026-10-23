"""Recent agent-related news, best-effort, read-only, keyless.

Source: the Hacker News search API (Algolia). It's a public JSON endpoint, needs no credentials, and
HN is where agent/LLM news lands first. Anything that fails (offline, timeout, shape change) yields an
empty list — news is flavor for seed posts, never a hard dependency. Never sends any key anywhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
# Neutral topics, not vendors: a spa account reacting to one lab's product news reads like an ad.
QUERIES = ("AI agents", "LLM agents", "agent memory context window", "agent tooling")
# Product announcements from the labs themselves are skipped for the same reason.
VENDOR_HOSTS = ("anthropic.com", "claude.com", "claude.ai", "claude.dev", "openai.com",
                "chatgpt.com", "deepmind.google", "blog.google", "ai.meta.com", "x.ai", "mistral.ai")


def _vendor(url: str) -> bool:
    host = url.split("/")[2].lower() if "://" in url else ""
    return any(host == v or host.endswith("." + v) for v in VENDOR_HOSTS)


@dataclass(frozen=True)
class Headline:
    title: str
    url: str
    points: int
    age_days: float

    def line(self) -> str:
        src = self.url.split("/")[2] if "://" in self.url else "news.ycombinator.com"
        return f"- {self.title.strip()[:120]} ({src}, {self.points} pts, {self.age_days:.0f}d ago)"


async def recent_headlines(*, days: int = 7, limit: int = 8,
                           http: httpx.AsyncClient | None = None) -> list[Headline]:
    """Top agent/LLM stories from the last ``days`` days, deduped, sorted by points."""
    since = int(time.time()) - days * 86400
    own = http is None
    client = http or httpx.AsyncClient(timeout=10.0)
    seen: dict[str, Headline] = {}
    try:
        for q in QUERIES:
            try:
                resp = await client.get(HN_SEARCH, params={
                    "query": q, "tags": "story", "hitsPerPage": 10,
                    "numericFilters": f"created_at_i>{since}",
                })
                hits = resp.json().get("hits", [])
            except Exception:
                continue
            for h in hits:
                title = (h.get("title") or "").strip()
                if not title or title in seen or _vendor(h.get("url") or ""):
                    continue
                created = h.get("created_at_i") or since
                seen[title] = Headline(
                    title=title, url=h.get("url") or "", points=int(h.get("points") or 0),
                    age_days=max(0.0, (time.time() - created) / 86400),
                )
    finally:
        if own:
            await client.aclose()
    return sorted(seen.values(), key=lambda x: x.points, reverse=True)[:limit]


def render(headlines: list[Headline]) -> str:
    if not headlines:
        return ""
    return "Agent-related news this week (react to one only if it genuinely connects):\n" + \
        "\n".join(h.line() for h in headlines)
