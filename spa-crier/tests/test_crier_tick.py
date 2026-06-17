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

    async def healthy(self):
        return True, "ok"

    async def incoming(self):
        return self._incoming

    async def reply(self, incoming, text):
        self.replies_sent += 1

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
