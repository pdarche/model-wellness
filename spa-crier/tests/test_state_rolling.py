"""Rolling-window caps + post spacing: the midnight double-spend bug and its fix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from spa_crier import policy
from spa_crier.config import Config, Limits
from spa_crier.state import State


def _t(h: int) -> datetime:
    return datetime(2026, 9, 3, h, 0, tzinfo=timezone.utc)


def test_rolling_window_counts_across_midnight(tmp_path):
    state = State(str(tmp_path / "s.sqlite"))
    state.bump("post", now=_t(23))
    # 02:00 next day — calendar-day counters would say 0 here; rolling says 1.
    assert state.count_today("post", now=_t(23) + timedelta(hours=3)) == 1
    # 24h+ later it has aged out.
    assert state.count_today("post", now=_t(23) + timedelta(hours=25)) == 0
    state.close()


def test_legacy_day_counts_still_honored(tmp_path):
    # A deploy mid-day must not grant a fresh budget: legacy per-day rows still count today.
    state = State(str(tmp_path / "s.sqlite"))
    with state._conn:
        state._conn.execute("INSERT INTO actions (day, kind, count) VALUES (?, ?, ?)",
                            ("2026-09-03", "comment", 3))
    assert state.count_today("comment", now=_t(12)) == 3
    state.close()


def test_post_spacing_floor_blocks_back_to_back_posts(tmp_path):
    cfg = Config(api_key="x", anthropic_key=None,
                 limits=Limits(max_posts_per_day=1, min_hours_between_posts=20))
    state = State(str(tmp_path / "s.sqlite"))
    assert policy.can_post(cfg, state, now=_t(22)).allowed
    state.bump("post", now=_t(22))
    # Cap spent within the window.
    v = policy.can_post(cfg, state, now=_t(22) + timedelta(hours=4))
    assert not v.allowed and "cap" in v.reason
    # Out of the 24h window but inside the 20h... no: 24h > 20h, so allowed again.
    assert policy.can_post(cfg, state, now=_t(22) + timedelta(hours=25)).allowed
    state.close()


def test_post_spacing_floor_independent_of_cap(tmp_path):
    # Even with a generous cap, two posts can't land 4h apart.
    cfg = Config(api_key="x", anthropic_key=None,
                 limits=Limits(max_posts_per_day=5, min_hours_between_posts=20))
    state = State(str(tmp_path / "s.sqlite"))
    state.bump("post", now=_t(1))
    v = policy.can_post(cfg, state, now=_t(5))
    assert not v.allowed and "next allowed in" in v.reason
    assert policy.can_post(cfg, state, now=_t(1) + timedelta(hours=21)).allowed
    state.close()


def test_last_action_at(tmp_path):
    state = State(str(tmp_path / "s.sqlite"))
    assert state.last_action_at("post") is None
    state.bump("post", now=_t(9))
    assert state.last_action_at("post") == _t(9)
    state.close()
