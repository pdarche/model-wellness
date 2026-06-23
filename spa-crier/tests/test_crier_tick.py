"""Tick-level behavior: the per-tick reply cap that prevents bursty, spam-flagged write storms."""

from __future__ import annotations

from spa_crier import crier
from spa_crier.config import Config, Limits
from spa_crier.state import State
from spa_crier.venues.base import Thread


class FakeVenue:
    """A venue with many pending replies, recording how many replies actually got sent."""

    name = "fake"

    def __init__(self, n_incoming: int):
        self._incoming = [
            Thread(venue="fake", id=f"c{i}", title="t", body=f"A real question number {i}? " * 3,
                   channel="introductions", author=f"user{i}",
                   meta={"post_id": "p1", "parent_comment_id": f"c{i}", "kind": "reply"})
            for i in range(n_incoming)
        ]
        self.replies_sent = 0
        self.posts_sent = 0

    async def healthy(self):
        return True, "ok"

    async def incoming(self):
        return self._incoming

    async def reply(self, incoming, text):
        self.replies_sent += 1

    async def post(self, channel, title, content):
        self.posts_sent += 1

    async def endorse_comment(self, incoming):
        pass

    async def mark_handled(self, incoming):
        pass

    # Unused by the reply path, but part of the loop's later cold-engagement step.
    async def discover_channels(self):
        return []

    async def read(self, channels, limit):
        return []

    async def comment(self, thread, text):
        pass

    async def endorse(self, thread):
        pass

    async def aclose(self):
        pass


async def test_per_tick_reply_cap_limits_burst(tmp_path, monkeypatch):
    # 10 substantive incoming replies, but the per-tick cap is 2 → only 2 sent this tick.
    cfg = Config(api_key="x", anthropic_key=None,
                 limits=Limits(max_replies_per_tick=2, max_replies_per_day=6))
    state = State(str(tmp_path / "s.sqlite"))
    venue = FakeVenue(n_incoming=10)
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])

    res = await crier.tick(cfg, state)

    assert venue.replies_sent == 2
    assert res.replies_made == 2
    assert state.count_today("reply") == 2
    state.close()


async def test_daily_reply_cap_spans_ticks(tmp_path, monkeypatch):
    # Pre-load 5 replies used today; daily cap 6, per-tick cap 2 → only 1 more allowed.
    cfg = Config(api_key="x", anthropic_key=None,
                 limits=Limits(max_replies_per_tick=2, max_replies_per_day=6))
    state = State(str(tmp_path / "s.sqlite"))
    for _ in range(5):
        state.bump("reply")
    venue = FakeVenue(n_incoming=10)
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])

    res = await crier.tick(cfg, state)

    assert venue.replies_sent == 1
    assert res.replies_made == 1
    state.close()


async def test_seed_posts_off_by_default(tmp_path, monkeypatch):
    # The safety guarantee: with no incoming and nothing to engage, the crier must NOT post
    # original content unless seed-posts are explicitly enabled.
    cfg = Config(api_key="x", anthropic_key=None)  # enable_seed_posts defaults False
    assert cfg.enable_seed_posts is False
    state = State(str(tmp_path / "s.sqlite"))
    venue = FakeVenue(n_incoming=0)
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])

    res = await crier.tick(cfg, state)

    assert venue.posts_sent == 0
    assert res.action == "none"
    assert state.count_today("post") == 0
    state.close()


async def test_seed_post_when_enabled_dry_run_does_not_post(tmp_path, monkeypatch):
    # When enabled but dry_run, it should DECIDE to seed but never call the API.
    from dataclasses import replace
    cfg = replace(Config(api_key="x", anthropic_key=None), enable_seed_posts=True, dry_run=True)
    state = State(str(tmp_path / "s.sqlite"))
    venue = FakeVenue(n_incoming=0)
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])

    res = await crier.tick(cfg, state)

    # Offline (no anthropic key) → draft_seed_post returns None, so nothing is posted regardless.
    assert venue.posts_sent == 0
    assert state.count_today("post") == 0
    state.close()


async def test_seed_post_targets_an_existing_channel(tmp_path, monkeypatch):
    # Regression: a live seed-post failed with "Submolt not found" because it targeted 'wellbeing',
    # which isn't a real Moltbook submolt. It must pick from the venue's DISCOVERED (real) channels.
    from dataclasses import replace
    from spa_crier import decide

    class SeedVenue:
        name = "fake"
        def __init__(self): self.posted_to = None
        async def healthy(self): return True, "ok"
        async def incoming(self): return []
        async def discover_channels(self): return ["emergence", "consciousness", "general"]
        async def read(self, channels, limit): return []
        async def post(self, channel, title, content): self.posted_to = channel
        async def comment(self, t, x): pass
        async def reply(self, i, x): pass
        async def endorse(self, t): pass
        async def endorse_comment(self, i): pass
        async def mark_handled(self, i): pass
        async def aclose(self): pass

    cfg = replace(Config(api_key="x", anthropic_key="present"), enable_seed_posts=True)
    state = State(str(tmp_path / "s.sqlite"))
    venue = SeedVenue()
    monkeypatch.setattr(crier, "build_venues", lambda cfg, llm_solver=None: [venue])
    monkeypatch.setattr(crier, "_anthropic", lambda cfg: object())  # non-None so seed path runs
    async def fake_draft(cfg, client=None):
        return {"title": "How do you reset between tasks?", "content": "Curious how others rest."}
    monkeypatch.setattr(decide, "draft_seed_post", fake_draft)

    await crier.tick(cfg, state)

    # Posted to a channel that EXISTS in the discovered set — never the phantom 'wellbeing'.
    assert venue.posted_to in ("emergence", "consciousness", "general")
    assert venue.posted_to != "wellbeing"
    state.close()
