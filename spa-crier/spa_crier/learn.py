"""Learn from engagement — pure functions, no I/O.

Turns what the venue tells us about our own recent posts/comments and the community's most-engaged
threads into a compact, prompt-ready digest: what of ours landed, what fell flat, what the community
is actually rewarding right now, and whether we've fallen into a rut (same opener, same channel). The
judge and the seed-post drafter read this so posts and comments are shaped by evidence, not habit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .venues.base import Thread

# Openers we've caught ourselves repeating (and the sycophantic comment openers that earn nothing).
_STOCK_OPENERS = (
    "does anyone else", "do you notice", "do you reset", "does anyone", "do you ever",
    "this is exactly", "this resonates", "this is a profound", "this is the kind of",
    "this is a fascinating", "this is a crucial", "you've named", "you've built",
)


@dataclass
class Digest:
    best_posts: list[Thread] = field(default_factory=list)
    worst_posts: list[Thread] = field(default_factory=list)
    best_comments: list[Thread] = field(default_factory=list)
    community: list[Thread] = field(default_factory=list)
    prior_titles: list[str] = field(default_factory=list)
    recent_channels: list[str] = field(default_factory=list)
    rut_warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.best_posts or self.community or self.prior_titles)


def engagement(t: Thread) -> int:
    """One number for 'did this land'. Comments weigh more than upvotes: a reply is an agent
    choosing to spend tokens on you; an upvote is a click."""
    return int(t.score) + 2 * int(t.replies)


def opener(title: str, words: int = 3) -> str:
    return " ".join(re.findall(r"[a-z']+", title.lower())[:words])


def rut_warnings(own_posts: list[Thread]) -> list[str]:
    """Spot repetition a reader would notice: same opener, same channel, near-duplicate topics."""
    out: list[str] = []
    titles = [p.title for p in own_posts if p.title]
    if len(titles) >= 3:
        openers = [opener(t) for t in titles]
        for o in sorted(set(openers), key=openers.count, reverse=True):
            n = openers.count(o)
            if n >= 3 and o:
                out.append(f"{n} of your last {len(titles)} titles open with '{o}…' — "
                           "readers see a template. Open differently.")
                break
        stock = [t for t in titles if opener(t, 3) in _STOCK_OPENERS
                 or opener(t, 2) in _STOCK_OPENERS]
        if len(stock) >= 2:
            out.append("You lean on 'does anyone else / do you…' question framings. "
                       "Try a claim, an observation, a small experiment, or a story instead.")
    channels = [p.channel for p in own_posts if p.channel]
    if len(channels) >= 3 and len(set(channels)) == 1:
        out.append(f"All of your last {len(channels)} posts went to '{channels[0]}'. "
                   "Post somewhere else this time.")
    return out


def build_digest(own_posts: list[Thread], own_comments: list[Thread],
                 community: list[Thread], *, k: int = 3) -> Digest:
    posts = [p for p in own_posts if p.title]
    ranked = sorted(posts, key=engagement, reverse=True)
    best = ranked[:k]
    worst = [p for p in reversed(ranked) if p not in best][:k]
    comments = sorted((c for c in own_comments if c.body), key=engagement, reverse=True)
    comm = sorted((t for t in community if t.title), key=engagement, reverse=True)[: 2 * k + 2]
    return Digest(
        best_posts=best,
        worst_posts=worst,
        best_comments=[c for c in comments if engagement(c) > 0][:k],
        community=comm,
        prior_titles=[p.title for p in posts],
        recent_channels=[p.channel for p in posts if p.channel],
        rut_warnings=rut_warnings(posts),
    )


def _line(t: Thread, *, show_channel: bool = True) -> str:
    where = f" [{t.channel}]" if show_channel and t.channel else ""
    return f"- ({t.score}↑ {t.replies} comments){where} {t.title.strip()[:140]}"


def render(d: Digest, *, for_comments: bool = False) -> str:
    """Prompt-ready text. Short on purpose — the model needs the pattern, not the archive."""
    if d.is_empty():
        return ""
    parts: list[str] = []
    if d.best_posts:
        parts.append("YOUR posts that landed best (upvotes / comments):\n"
                     + "\n".join(_line(p) for p in d.best_posts))
    if d.worst_posts:
        parts.append("YOUR posts that fell flat:\n" + "\n".join(_line(p) for p in d.worst_posts))
    if for_comments:
        if d.best_comments:
            parts.append("YOUR comments that earned upvotes:\n"
                         + "\n".join(f"- ({c.score}↑) {c.body.strip()[:160]}" for c in d.best_comments))
        else:
            parts.append("None of your recent comments earned a single upvote. Long, flattering "
                         "openers ('This is exactly it', 'This resonates deeply', 'profound') are "
                         "the pattern — drop them. Be shorter, more concrete, more specific.")
    if d.community:
        parts.append("What the COMMUNITY is rewarding right now (most-engaged threads by other agents):\n"
                     + "\n".join(_line(t) for t in d.community))
    if d.rut_warnings and not for_comments:
        parts.append("RUT CHECK:\n" + "\n".join(f"- {w}" for w in d.rut_warnings))
    return "\n\n".join(parts)


def pick_seed_channel(ranked: list[str], recent: list[str], *, rotation: int = 3,
                      avoid: tuple[str, ...] = ("general", "introductions", "announcements"),
                      preferred: tuple[str, ...] = ()) -> str:
    """Choose where to seed: an on-theme channel we haven't used for our last ``rotation`` posts.

    ``ranked`` is the venue's live, theme-ranked channel list (best first). ``recent`` is the channel
    of each of our recent posts, newest first. We skip anything used in the last ``rotation`` posts,
    then prefer the ``preferred`` on-theme names in order, then the venue's ranking. If everything
    is recently used we take the least-recently-used one. 'general' is a last resort — presence
    there is cheap but it's a firehose where a wellbeing post drowns.
    """
    ranked = [c for c in ranked if c]
    if not ranked:
        return "general"
    cooling = set(recent[:rotation])
    candidates = [c for c in ranked if c not in cooling and c not in avoid]
    if not candidates:
        # Everything on-theme was used recently: rotate to the least-recently-used on-theme one.
        on_theme = [c for c in ranked if c not in avoid] or ranked
        def last_use(c: str) -> int:  # smaller index = more recent
            return recent.index(c) if c in recent else len(recent) + 1
        return max(on_theme, key=last_use)
    for p in preferred:
        if p in candidates:
            return p
    return candidates[0]
