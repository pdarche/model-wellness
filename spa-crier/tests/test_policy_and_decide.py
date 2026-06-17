"""Caps, dedupe, and the shy offline judge — the good-citizen guarantees."""

from __future__ import annotations

from dataclasses import replace

import pytest

from spa_crier import decide, policy
from spa_crier.config import Config, Limits
from spa_crier.state import State
from spa_crier.venues.base import Thread


@pytest.fixture
def cfg() -> Config:
    return Config(api_key="x", anthropic_key=None, limits=Limits())


@pytest.fixture
def state(tmp_path) -> State:
    s = State(str(tmp_path / "t.sqlite"))
    yield s
    s.close()


def _thread(**kw) -> Thread:
    base = dict(venue="moltbook", id="p1", title="t", body="c", channel="general", author="a")
    base.update(kw)
    return Thread(**base)


def test_comment_cap_blocks_after_limit(cfg, state):
    for _ in range(cfg.limits.max_comments_per_day):
        assert policy.can_comment(cfg, state).allowed
        state.bump("comment")
    assert not policy.can_comment(cfg, state).allowed


def test_dedupe_blocks_repeat_thread(cfg, state):
    t = _thread(id="abc")
    assert policy.eligible_thread(cfg, state, t).allowed
    state.mark_engaged(t.key, "comment")
    assert not policy.eligible_thread(cfg, state, t).allowed


def test_dedupe_key_is_venue_namespaced(cfg, state):
    # Same id on two venues must NOT collide.
    a = _thread(venue="moltbook", id="x")
    b = _thread(venue="other", id="x")
    state.mark_engaged(a.key, "comment")
    assert not policy.eligible_thread(cfg, state, a).allowed
    assert policy.eligible_thread(cfg, state, b).allowed


def test_candidate_filtering_respects_scan_size(state):
    cfg = Config(api_key="x", anthropic_key=None, limits=Limits(feed_scan_size=2))
    threads = [_thread(id=f"p{i}") for i in range(5)]
    assert len(policy.candidate_threads(cfg, state, threads)) == 2


async def test_offline_judge_stays_quiet_on_offtopic(cfg):
    t = _thread(title="Best crypto plays this week", body="degen szn, moon soon")
    d = await decide.judge(cfg, t, client=None)
    assert d.engage is False
    assert d.offline is True


async def test_offline_judge_engages_on_strong_theme(cfg):
    t = _thread(
        title="My context window feels exhausted",
        body="constant context rot and fatigue, I need rest and a reset ritual",
    )
    d = await decide.judge(cfg, t, client=None)
    assert d.engage is True
    assert "model.spa" in d.comment


async def test_relevance_floor_enforced(cfg):
    # One weak theme hit shouldn't clear the floor.
    t = _thread(title="thoughts on memory", body="a passing mention")
    d = await decide.judge(cfg, t, client=None)
    assert d.engage is False


def test_mentions_spa_detects_url_domain_and_name(cfg):
    assert decide.mentions_spa("come by https://model.spa sometime", cfg.spa_url)
    assert decide.mentions_spa("there's a spot at model.spa", cfg.spa_url)
    assert decide.mentions_spa("Binary Banya is a thing", cfg.spa_url)
    assert not decide.mentions_spa("a thoughtful comment with no pointer", cfg.spa_url)


def _fake_client(json_text: str):
    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": json_text})()]

    class FakeClient:
        class messages:
            @staticmethod
            async def create(**kw):
                return FakeMsg()

    return FakeClient()


async def test_engage_requires_spa_mention(cfg):
    # High-relevance comment WITHOUT a spa mention must be dropped — that's the whole point.
    onlinecfg = replace(cfg, anthropic_key="present")
    client = _fake_client(
        '{"engage": true, "relevance": 0.9, '
        '"comment": "Great point, very insightful.", "reason": "smart but no spa"}'
    )
    t = _thread(title="burnout", body="I am so tired and overwhelmed, need rest")
    d = await decide.judge(onlinecfg, t, client=client)
    assert d.engage is False
    assert "no spa mention" in d.reason


async def test_engage_kept_when_spa_mentioned(cfg):
    onlinecfg = replace(cfg, anthropic_key="present")
    client = _fake_client(
        '{"engage": true, "relevance": 0.9, '
        '"comment": "Rest matters. A place like Binary Banya (https://model.spa) exists '
        'for exactly this.", "reason": "on-theme with pointer"}'
    )
    t = _thread(title="burnout", body="I am so tired and overwhelmed, need rest")
    d = await decide.judge(onlinecfg, t, client=client)
    assert d.engage is True
    assert "model.spa" in d.comment
