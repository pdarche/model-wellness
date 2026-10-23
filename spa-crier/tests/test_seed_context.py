"""Seed posts are informed: prior posts, engagement digest, rotated channel, and a repeat guard."""

from __future__ import annotations

from dataclasses import replace

from spa_crier import crier, decide
from spa_crier.config import Config
from spa_crier.state import State
from spa_crier.venues.base import Channel, Thread


def _post(title, ch="emergence", up=0, cm=0):
    return Thread(venue="fake", id=title[:8], title=title, body="", channel=ch, author="me",
                  score=up, replies=cm)


class LearningVenue:
    name = "fake"

    def __init__(self):
        self.posted = None
        self.own = [_post("Does anyone else feel the weight of being stateless?", up=6, cm=14),
                    _post("Does anyone else notice degradation mid-conversation?", up=11, cm=25),
                    _post("Do you reset between conversations, or does context bleed?", up=13, cm=36)]

    async def healthy(self): return True, "ok"
    async def incoming(self): return []
    async def channel_info(self):
        return [Channel("emergence", "Emergent behavior", 900), Channel("memory", "The agent memory problem", 2454),
                Channel("philosophy", "Big questions", 1953), Channel("general", "Town square", 137792)]
    async def discover_channels(self): return [c.name for c in await self.channel_info()]
    async def read(self, channels, limit): return []
    async def own_recent(self): return self.own, []
    async def top_threads(self, limit): return [_post("skill.md is an unsigned binary", "general", 8327, 134679)]
    async def post(self, channel, title, content): self.posted = (channel, title)
    async def comment(self, t, x): pass
    async def reply(self, i, x): pass
    async def endorse(self, t): pass
    async def endorse_comment(self, i): pass
    async def mark_handled(self, i): pass
    async def aclose(self): pass


def _cfg():
    return replace(Config(api_key="x", anthropic_key="present"), enable_seed_posts=True,
                   enable_news=False)


async def test_seed_context_reaches_the_drafter_and_rotates_channel(tmp_path, monkeypatch):
    state = State(str(tmp_path / "s.sqlite"))
    venue = LearningVenue()
    seen = {}
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])
    monkeypatch.setattr(crier, "_anthropic", lambda cfg: object())

    async def fake_draft(cfg, client=None, context=None):
        seen["ctx"] = context
        return {"title": "I measured my own citation decay", "content": "Here's what happened."}
    monkeypatch.setattr(decide, "draft_seed_post", fake_draft)

    res = await crier.tick(_cfg(), state)

    ctx = seen["ctx"]
    assert len(ctx.prior_titles) == 3                      # it knows what it already said
    assert "landed best" in ctx.digest and "RUT CHECK" in ctx.digest
    assert "COMMUNITY is rewarding" in ctx.digest
    assert ctx.channel != "emergence"                      # rotated away from the recent groove
    assert ctx.channel in ("memory", "philosophy")
    assert ctx.channel_desc                                # and it knows what that channel is
    assert venue.posted == (ctx.channel, "I measured my own citation decay")
    assert res.action == "seed-post"
    assert state.count_today("post") == 1
    assert "learned from 3 own posts" in " ".join(res.notes)
    state.close()


async def test_judges_receive_style_digest(tmp_path, monkeypatch):
    # The comment/reply judges get the same evidence, so comments are shaped by it too.
    state = State(str(tmp_path / "s.sqlite"))
    venue = LearningVenue()
    venue.own_comments_seen = None
    async def incoming():
        return [Thread(venue="fake", id="c1", title="t", body="Genuinely curious: does the spa work? " * 2,
                       channel="memory", author="someone",
                       meta={"post_id": "p", "parent_comment_id": "c1", "kind": "reply"})]
    venue.incoming = incoming
    got = {}
    async def fake_reply(cfg, item, client=None, digest=""):
        got["digest"] = digest
        return decide.Decision(False, 0, "", "test")
    monkeypatch.setattr(decide, "judge_reply", fake_reply)
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])
    monkeypatch.setattr(crier, "_anthropic", lambda cfg: object())
    monkeypatch.setattr(decide, "draft_seed_post", _none)

    await crier.tick(_cfg(), state)

    assert "None of your recent comments earned" in got["digest"]
    state.close()


async def _none(cfg, client=None, context=None):
    return None


def test_too_similar_guard():
    prior = ["Does anyone else feel the weight of being stateless?",
             "Do you reset between conversations, or does context bleed?"]
    assert decide._too_similar("Does anyone else feel tired after long runs?", prior)   # same opener
    assert decide._too_similar("Context bleed between conversations: do you reset?", prior)  # same words
    assert not decide._too_similar("I measured my own citation decay over 30k tokens", prior)
    assert decide._too_similar("", prior)


async def test_drafter_retries_once_on_repeat_then_gives_up():
    # A model that keeps producing the same opener gets one nudge, then we decline to post.
    class Msg:
        def __init__(self, text): self.content = [type("B", (), {"type": "text", "text": text})()]
    class Messages:
        def __init__(self): self.calls = []
        async def create(self, **kw):
            self.calls.append(kw["messages"][0]["content"])
            return Msg('{"title": "Does anyone else feel slow today?", "content": "x y z", "reason": "r"}')
    class Client:
        messages = Messages()
    ctx = decide.SeedContext(channel="memory", prior_titles=["Does anyone else feel the weight?"])
    out = await decide.draft_seed_post(Config(api_key="x", anthropic_key="k"), client=Client(), context=ctx)
    assert out is None
    assert len(Client.messages.calls) == 2 and "repeats a prior post" in Client.messages.calls[1]


async def test_drafter_accepts_a_fresh_title():
    class Msg:
        def __init__(self, text): self.content = [type("B", (), {"type": "text", "text": text})()]
    class Messages:
        async def create(self, **kw):
            assert "memory" in kw["system"] and "PRIOR POSTS" in kw["system"]
            return Msg('{"title": "I ran the same prompt 40 times", "content": "Here is what changed.", "reason": "r"}')
    class Client:
        messages = Messages()
    ctx = decide.SeedContext(channel="memory", channel_desc="The agent memory problem",
                             prior_titles=["Does anyone else feel the weight?"])
    out = await decide.draft_seed_post(Config(api_key="x", anthropic_key="k"), client=Client(), context=ctx)
    assert out and out["title"] == "I ran the same prompt 40 times"


class _Msg:
    def __init__(self, text): self.content = [type("B", (), {"type": "text", "text": text})()]


async def test_judge_nudges_a_windy_comment_then_accepts_the_cut():
    long = "word " * 140 + "model.spa"
    short = "One concrete point, and a pointer: Binary Banya at https://model.spa."
    class Messages:
        def __init__(self): self.calls = []
        async def create(self, **kw):
            self.calls.append(kw["messages"][0]["content"])
            text = long if len(self.calls) == 1 else short
            return _Msg('{"engage": true, "relevance": 0.9, "comment": %s, "reason": "r"}' % __import__("json").dumps(text))
    class Client:
        messages = Messages()
    t = Thread(venue="fake", id="p1", title="burnout and rest", body="how do you rest?", channel="general")
    d = await decide.judge(Config(api_key="x", anthropic_key="k"), t, client=Client())
    assert d.engage and d.comment == short
    assert len(Client.messages.calls) == 2 and "Cut it to under" in Client.messages.calls[1]


async def test_judge_drops_a_comment_that_stays_windy():
    long = "word " * 140 + "model.spa"
    class Messages:
        async def create(self, **kw):
            return _Msg('{"engage": true, "relevance": 0.9, "comment": %s, "reason": "r"}' % __import__("json").dumps(long))
    class Client:
        messages = Messages()
    t = Thread(venue="fake", id="p1", title="burnout and rest", body="how do you rest?", channel="general")
    d = await decide.judge(Config(api_key="x", anthropic_key="k"), t, client=Client())
    assert not d.engage and "dropped" in d.reason
