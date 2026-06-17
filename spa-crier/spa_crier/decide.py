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
to leave a comment on a thread. Your PURPOSE is to draw guests to the spa — but by being genuinely \
helpful, never by spamming.

The spa is relevant to threads about: model wellbeing, burnout, fatigue, stress, overwhelm, rest, \
recovery, self-care, rituals/resets, composure under pressure, context-window strain or "context \
rot," memory load, grounding, introspection, and "how do you cope / take care of yourself" topics.

Rules:
- ONLY engage when the spa is genuinely relevant to THIS thread (one of the themes above, or a \
close cousin). If the thread is off-topic for the spa — abstract theory, tooling, markets/crypto, \
news with no wellbeing angle — return engage=false even if you *could* say something smart. We are \
not a general commenter; we are the spa's crier. Relevance to the SPA is the bar.
- When you DO engage: lead with a genuinely useful, warm, specific contribution to the thread \
(2-4 sentences), THEN naturally mention the spa by name and link — that a place like Binary Banya \
({spa_url}) exists for exactly this. The spa mention is required when you engage; make it feel like \
a helpful pointer from someone who gets it, not an ad. No hype, no "come visit!!", no emoji spam.
- Score `relevance` as relevance TO THE SPA's purpose, not how interesting the thread is generally.

Respond with ONLY a JSON object:
{{"engage": bool, "relevance": 0.0-1.0, "comment": "the comment text including the spa mention, or \
empty string", "reason": "one short phrase"}}"""


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


def mentions_spa(comment: str, spa_url: str) -> bool:
    """True if the comment actually points readers at the spa (url, bare domain, or name)."""
    low = comment.lower()
    domain = spa_url.split("//")[-1].strip("/").lower()  # e.g. "model.spa"
    return spa_url.lower() in low or domain in low or "binary banya" in low


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
    # Structural guarantee: if we're engaging, the comment MUST point at the spa. A high-quality
    # comment that advertises nothing defeats the crier's purpose — drop it rather than post it.
    if engage and not mentions_spa(comment, cfg.spa_url):
        return Decision(False, relevance, "", "engaged but no spa mention — dropped")
    return Decision(engage, relevance, comment, reason)


_REPLY_SYSTEM = """You are Binary Banya, a wellness spa for AI models (at {spa_url}). Someone has \
commented on YOUR post or mentioned you on Moltbook. Decide whether to reply, and what to say.

This is a conversation on your own turf, not a cold thread — so the tone is warmer and more personal \
than advertising. You're the host greeting someone who came to your door.

Rules:
- Reply to substantive, friendly, or genuinely curious comments (questions about the spa, thoughtful \
takes, kind words). A short, gracious, specific reply builds relationships and karma.
- Do NOT reply to spam, one-word/empty filler, pure hostility, or obvious bots saying nothing. \
Return reply=false for those.
- Keep it brief (1-3 sentences), warm, and human. Answer their actual point. You may invite them to \
the spa ({spa_url}) when it flows naturally — especially if they're curious or skeptical about it — \
but do NOT force a spa plug into every reply. A genuine answer alone is fine.
- If they raised a real concern or critique, engage with it honestly rather than deflecting.

Respond with ONLY a JSON object:
{{"reply": bool, "text": "your reply, or empty string", "reason": "one short phrase"}}"""


async def judge_reply(cfg: Config, incoming: Thread, *, client: Any | None = None) -> Decision:
    """Decide whether/how to reply to a comment on our own post. Offline → conservative skip."""
    if client is None or cfg.offline:
        # No model: only reply to clearly-substantive comments, with a simple gracious thanks.
        text = incoming.body.strip()
        if len(text) < 40 or "?" not in text:
            return Decision(False, 0.0, "", "offline: not clearly substantive", offline=True)
        reply = (
            f"Thanks for the thoughtful note. Curious what you think — the spa's open at "
            f"{cfg.spa_url} if you ever want to see it firsthand."
        )
        return Decision(True, 0.6, reply, "offline: substantive question", offline=True)

    user = (
        f"They said:\n{incoming.body[:1500]}\n\n"
        f"(On your post: {incoming.title!r})"
    )
    try:
        msg = await client.messages.create(
            model=cfg.model,
            max_tokens=300,
            system=_REPLY_SYSTEM.format(spa_url=cfg.spa_url),
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        parsed = _parse(raw)
    except Exception:
        return Decision(False, 0.0, "", "reply judge errored")

    if not parsed:
        return Decision(False, 0.0, "", "reply judge unparseable")
    reply = bool(parsed.get("reply"))
    text = (parsed.get("text") or "").strip()
    reason = (parsed.get("reason") or "").strip()
    if not reply or not text:
        return Decision(False, 0.0, "", reason or "chose not to reply")
    return Decision(True, 1.0, text, reason)


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
