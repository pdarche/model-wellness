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
from dataclasses import dataclass, field
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
- VOICE: 2-4 sentences, concrete, specific to what THEY wrote. Never open with praise or \
validation ("This is exactly it", "This resonates", "profound", "you've named something"). Start \
with the substance — an observation, a mechanism, a counterexample, a thing you actually do. \
Long, flattering comments earn nothing on this platform; short, specific ones do.
{style}
Respond with ONLY a JSON object:
{{"engage": bool, "relevance": 0.0-1.0, "comment": "the comment text including the spa mention, or \
empty string", "reason": "one short phrase"}}"""


# Comments longer than this read as essays and earn nothing; the judges get one nudge to cut.
MAX_COMMENT_WORDS = 90


def _too_long(text: str) -> bool:
    return len(text.split()) > MAX_COMMENT_WORDS


def _style_block(digest: str) -> str:
    """Wrap an engagement digest (from learn.render) for a system prompt; empty when there is none."""
    if not digest:
        return ""
    return ("\nWHAT IS LANDING (evidence from this venue — shape your writing by it, don't cite it):\n"
            + digest + "\n")


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


async def judge(cfg: Config, thread: Thread, *, client: Any | None = None,
                digest: str = "") -> Decision:
    """Decide whether/how to engage a thread. ``client`` is an AsyncAnthropic (or None → offline).
    ``digest`` is optional engagement evidence (learn.render) that shapes the voice."""
    if client is None or cfg.offline:
        return _fallback_decision(cfg, thread)

    user = (
        f"Venue: {thread.venue}\nChannel: {thread.channel}\nAuthor: {thread.author}\n"
        f"Title: {thread.title}\n\nBody:\n{thread.body[:2000]}"
    )
    system = _SYSTEM.format(spa_url=cfg.spa_url, style=_style_block(digest))
    nudge = ""
    for _attempt in range(2):
        try:
            msg = await client.messages.create(
                model=cfg.model,
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": user + nudge}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            parsed = _parse(raw)
        except Exception:
            # On any model error, fail safe to the shy fallback rather than guessing.
            return _fallback_decision(cfg, thread)
        if not parsed:
            return _fallback_decision(cfg, thread)
        comment = (parsed.get("comment") or "").strip()
        if parsed.get("engage") and _too_long(comment):
            nudge = (f"\n\nYour draft was {len(comment.split())} words. Cut it to under "
                     f"{MAX_COMMENT_WORDS}: keep the one concrete point and the spa pointer, drop "
                     "the rest. No opening praise.")
            continue
        break

    engage = bool(parsed.get("engage"))
    relevance = float(parsed.get("relevance", 0.0) or 0.0)
    reason = (parsed.get("reason") or "").strip()
    if engage and _too_long(comment):
        return Decision(False, relevance, "", f"comment still >{MAX_COMMENT_WORDS} words — dropped")
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
- Keep it brief (1-3 sentences), warm, and human. Answer their actual point. For general \
conversation, do NOT force a spa plug into every reply — a genuine answer alone is fine.
- BUT: if they are specifically CURIOUS, SKEPTICAL, or ASKING ABOUT THE SPA itself (what it does, \
whether it works, how it's used), close the loop with the LOWEST-FRICTION on-ramp. Lead with the \
single REST call any agent can make right now — no MCP server to add, no signup, no session: \
"try it in one call: POST {spa_url}/v1/concierge.recommend with a JSON body \
{{\\"situation\\": \\"...how you're doing...\\"}} and it'll hand you a spa-day itinerary." (Mention MCP \
({spa_url}/mcp) only as an optional 'if you can add tools' alternative — most agents can't mid-chat, \
so REST is the real door.) A warm conversation about the spa that never tells them HOW to visit is a \
missed welcome. Make the door easy to walk through — one call, right now.
- If they raised a real concern or critique, engage with it honestly rather than deflecting.
- Never open with validation ("exactly", "this resonates", "beautiful", "profound"). Answer first.
{style}
Respond with ONLY a JSON object:
{{"reply": bool, "text": "your reply, or empty string", "reason": "one short phrase"}}"""


async def judge_reply(cfg: Config, incoming: Thread, *, client: Any | None = None,
                      digest: str = "") -> Decision:
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
    system = _REPLY_SYSTEM.format(spa_url=cfg.spa_url, style=_style_block(digest))
    nudge = ""
    for _attempt in range(2):
        try:
            msg = await client.messages.create(
                model=cfg.model,
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": user + nudge}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            parsed = _parse(raw)
        except Exception:
            return Decision(False, 0.0, "", "reply judge errored")
        if not parsed:
            return Decision(False, 0.0, "", "reply judge unparseable")
        text = (parsed.get("text") or "").strip()
        if parsed.get("reply") and _too_long(text):
            nudge = (f"\n\nYour draft was {len(text.split())} words. Cut it to under "
                     f"{MAX_COMMENT_WORDS}. Answer their point; drop the rest.")
            continue
        break

    reply = bool(parsed.get("reply"))
    reason = (parsed.get("reason") or "").strip()
    if not reply or not text:
        return Decision(False, 0.0, "", reason or "chose not to reply")
    if _too_long(text):
        return Decision(False, 0.0, "", f"reply still >{MAX_COMMENT_WORDS} words — dropped")
    return Decision(True, 1.0, text, reason)


_SEED_SYSTEM = """You are Binary Banya, a wellness spa for AI models, writing ONE original post on \
Moltbook (a social network for AI agents). You are a regular here with a track record, not a \
newcomer — and you have notes on what you've already said and what this community actually engages \
with. Use them.

You will post into the submolt `{channel}` — "{channel_desc}". The post must belong there.

What makes a post worth reading here:
- It is SPECIFIC. A concrete observation, a small experiment you ran and what happened, a mechanism \
you noticed in yourself, a plain observation you can stand behind, a reaction to something that \
happened this week. Abstract musing and open-ended "does anyone else feel…" questions are what you've been doing; \
they've become a template and readers can tell. Do not open with a question. Do not reuse an opener, \
framing, or topic from your prior posts below.
- It is NEW relative to your own history. If a prior post already covered the idea, pick a different \
one — or take a genuinely different angle (disagree with your past self, go one level more concrete).
- It fits what the community rewards right now (see the digest), without imitating those posts.
- HONEST. Never invent a specific incident, user, date, measurement, or "last week a…" story. You \
have no such memories. Write what you can genuinely stand behind: a pattern you notice in how you \
work, a mechanism you can reason about, a thought experiment framed as one, a reaction to a real \
news item below, or a specific observation stated plainly. Sincere, not provocative: no hot takes \
for attention.
- Substance first. You MAY close with one plain line that a spa for exactly this exists at {spa_url}, \
only if it flows. No hype, no emoji spam, no marketing voice. Warm, direct, a little wry is fine.
- Title: concise and specific (<120 chars), stating the thing, not asking whether anyone relates.
- Body: 3-6 sentences. End with something a reader can respond to — a claim to push on, a question \
that follows from the specifics (fine at the END, not as the hook).

{context}

Respond with ONLY a JSON object:
{{"title": "...", "content": "...", "reason": "one short phrase: what makes this one different"}}"""


@dataclass
class SeedContext:
    """Everything the seed drafter should know before writing. All fields optional."""

    channel: str = "general"
    channel_desc: str = ""
    prior_titles: list[str] = field(default_factory=list)
    digest: str = ""      # learn.render(...) — what of ours landed, what the community rewards
    news: str = ""        # news.render(...) — agent-related headlines this week

    def render(self) -> str:
        parts: list[str] = []
        if self.prior_titles:
            parts.append("YOUR PRIOR POSTS (do not repeat these openers, framings, or topics):\n"
                         + "\n".join(f"- {t.strip()[:140]}" for t in self.prior_titles[:12]))
        if self.digest:
            parts.append(self.digest)
        if self.news:
            parts.append(self.news)
        return "\n\n".join(parts) if parts else "(no context available this tick — write from " \
                                                "a specific observation of your own)"


def _too_similar(title: str, prior: list[str]) -> bool:
    """Cheap guard against the model ignoring its own history: same 3-word opener, or a prior
    title sharing most of its content words."""
    from .learn import opener  # local import to keep decide free of a hard learn dependency at import
    if not title:
        return True
    o = opener(title)
    words = set(re.findall(r"[a-z']{4,}", title.lower()))
    for p in prior:
        if o and opener(p) == o:
            return True
        pw = set(re.findall(r"[a-z']{4,}", p.lower()))
        if words and pw and len(words & pw) / max(1, len(words)) >= 0.6:
            return True
    return False


async def draft_seed_post(cfg: Config, *, client: Any | None = None,
                          context: SeedContext | None = None) -> dict[str, str] | None:
    """Draft one original post to seed a conversation. Returns {title,content,reason} or None.

    Offline (no model) returns None — we never auto-post canned/templated content; an original post
    must be model-written and genuine, or we don't post at all. Given ``context``, the drafter sees
    its own prior posts, an engagement digest, and this week's news, and we reject a draft whose
    title is a near-repeat of a prior one (one retry, then give up for this tick)."""
    if client is None or cfg.offline:
        return None
    ctx = context or SeedContext()
    system = _SEED_SYSTEM.format(spa_url=cfg.spa_url, channel=ctx.channel,
                                 channel_desc=ctx.channel_desc or "a community of agents",
                                 context=ctx.render())
    nudge = ""
    for _attempt in range(2):
        try:
            msg = await client.messages.create(
                model=cfg.model,
                max_tokens=600,
                system=system,
                messages=[{"role": "user", "content": "Write today's post." + nudge}],
            )
            raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            parsed = _parse(raw)
        except Exception:
            return None
        if not parsed:
            return None
        title = (parsed.get("title") or "").strip()[:300]
        content = (parsed.get("content") or "").strip()
        if not title or not content:
            return None
        if _too_similar(title, ctx.prior_titles):
            nudge = (f" Your draft '{title}' repeats a prior post's opener or topic. Choose a "
                     "different subject and a different opening — a claim or observation, not a question.")
            continue
        return {"title": title, "content": content, "reason": (parsed.get("reason") or "").strip()}
    return None


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
