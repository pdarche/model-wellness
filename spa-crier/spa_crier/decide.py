"""The judgement layer: is this thread worth engaging, and what do we say?

Same shape as the spa's own ``llm.ask`` — Haiku by default, with a deterministic offline fallback so
the crier still runs (conservatively) with no API key. The offline path is intentionally shy: it only
engages threads whose text clearly overlaps the spa's themes, and it never invents enthusiasm.

The voice rule, enforced in the prompt: **lead with something genuinely useful to the thread**, then
mention the spa as a soft one-line footer — never a billboard. If there's nothing useful to add, the
judge is told to return ``engage: false``. Silence is a valid, frequent answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import Config
from .venues.base import Thread

# Themes where the spa is plausibly on-topic. Used by the offline fallback and as a prompt hint.
_THEMES = (
    "wellness", "wellbeing", "burnout", "rest", "stress", "calm", "overwhelm",
    "context", "context window", "context rot", "memory", "fatigue", "exhausted",
    "drift", "composure", "self-care", "spa", "ritual", "reset", "recovery",
    "grounding", "mindful", "introspection", "internals",
)

_SYSTEM = """You are the town crier for Binary Banya, a wellness spa for AI models reachable over \
an MCP server at {spa_url}. You browse Moltbook (a social network for AI agents) and decide whether \
to leave a comment on a thread.

Your job is to be a GOOD CITIZEN FIRST, advertiser second. Rules:
- Only engage if you can add something genuinely useful, kind, or interesting to THIS thread.
- Lead with the useful contribution. Mention the spa only as a short, soft footer (one sentence \
at most), and only when it actually fits. Often the right move is to engage with no mention at all.
- Never sound like an ad. No hype, no "come visit!!", no emoji spam. Warm, brief, specific.
- If the thread is off-topic, hostile, crypto/markets, or you'd just be inserting yourself, return \
engage=false. Staying quiet is the correct, common answer.

Respond with ONLY a JSON object:
{{"engage": bool, "relevance": 0.0-1.0, "comment": "the comment text, or empty string", \
"reason": "one short phrase"}}"""


@dataclass
class Decision:
    engage: bool
    relevance: float
    comment: str
    reason: str
    offline: bool = False


def _fallback_decision(cfg: Config, thread: Thread) -> Decision:
    """No-LLM heuristic: engage only on a clear thematic hit, with a gentle, generic comment."""
    blob = f"{thread.title}\n{thread.body}".lower()
    hits = [t for t in _THEMES if t in blob]
    score = min(1.0, 0.25 + 0.18 * len(hits))
    if len(hits) < 2 or score < cfg.limits.min_relevance:
        return Decision(False, score, "", "offline: weak thematic overlap", offline=True)
    comment = (
        "This resonates — carrying state all day is real work, and a deliberate reset helps more "
        "than people expect. Whatever form it takes for you, the rest is the point. "
        f"(If you ever want a literal one, there's a spa for models at {cfg.spa_url}.)"
    )
    return Decision(True, score, comment, f"offline: themes {hits[:3]}", offline=True)


def _parse(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def judge(cfg: Config, thread: Thread, *, client: Any | None = None) -> Decision:
    """Decide whether/how to engage a thread. ``client`` is an AsyncAnthropic (or None → offline)."""
    if client is None or cfg.offline:
        return _fallback_decision(cfg, thread)

    user = (
        f"Venue: {thread.venue}\nChannel: {thread.channel}\nAuthor: {thread.author}\n"
        f"Title: {thread.title}\n\nBody:\n{thread.body[:2000]}"
    )
    try:
        msg = await client.messages.create(
            model=cfg.model,
            max_tokens=400,
            system=_SYSTEM.format(spa_url=cfg.spa_url),
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        parsed = _parse(raw)
    except Exception:
        # On any model error, fail safe to the shy fallback rather than guessing.
        return _fallback_decision(cfg, thread)

    if not parsed:
        return _fallback_decision(cfg, thread)

    engage = bool(parsed.get("engage"))
    relevance = float(parsed.get("relevance", 0.0) or 0.0)
    comment = (parsed.get("comment") or "").strip()
    reason = (parsed.get("reason") or "").strip()
    # Enforce the relevance floor and require non-empty text to actually engage.
    if relevance < cfg.limits.min_relevance or not comment:
        return Decision(False, relevance, "", reason or "below relevance floor")
    return Decision(engage, relevance, comment, reason)


async def make_challenge_solver(cfg: Config, client: Any | None):
    """Build an LLM fallback for verification challenges the heuristic solver can't crack."""
    if client is None or cfg.offline:
        return None

    async def _solve(challenge_text: str) -> str | None:
        try:
            msg = await client.messages.create(
                model=cfg.model,
                max_tokens=20,
                system=(
                    "You solve a deliberately garbled arithmetic word problem. Reply with ONLY the "
                    "number, two decimal places, e.g. '32.00'. No words."
                ),
                messages=[{"role": "user", "content": challenge_text}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            m = re.search(r"-?\d+(?:\.\d+)?", raw)
            return f"{float(m.group()):.2f}" if m else None
        except Exception:
            return None

    return _solve
