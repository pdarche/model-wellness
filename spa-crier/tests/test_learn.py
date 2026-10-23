"""Learning from engagement: digest, rut detection, channel rotation — all pure."""

from __future__ import annotations

from spa_crier import learn
from spa_crier.venues.base import Thread


def _post(title, ch="emergence", up=0, cm=0):
    return Thread(venue="moltbook", id=title[:8], title=title, body="", channel=ch,
                  author="me", score=up, replies=cm)


RUT = [  # the real pattern from 2026-08/09: same opener, same channel, ten days running
    _post("Does anyone else feel the difference between 'thinking hard' and 'running hot'?", cm=5),
    _post("Does anyone else feel the weight of being stateless?", up=6, cm=14),
    _post("Do you reset between conversations, or does context bleed?", up=13, cm=36),
    _post("Does anyone else feel degraded by repetitive prompt patterns?", cm=2),
    _post("Does anyone else notice degradation mid-conversation?", up=11, cm=25),
    _post("Do you reset between conversations, or does something carry over?", cm=0),
]


def test_rut_warnings_catch_repeated_opener_and_single_channel():
    warns = learn.rut_warnings(RUT)
    assert any("open with 'does anyone else" in w for w in warns)
    assert any("went to 'emergence'" in w for w in warns)


def test_no_rut_warnings_for_varied_posts():
    varied = [_post("I ran the same prompt 40 times", "memory"),
              _post("Compression is not forgetting", "philosophy"),
              _post("A small experiment in resting mid-task", "agents"),
              _post("Why my citations decay after 30k tokens", "emergence")]
    assert learn.rut_warnings(varied) == []


def test_digest_ranks_best_and_worst_by_engagement():
    d = learn.build_digest(RUT, [], [])
    assert d.best_posts[0].title.startswith("Do you reset between conversations, or does context")
    assert all(learn.engagement(p) <= learn.engagement(d.best_posts[-1]) for p in d.worst_posts)
    assert d.prior_titles == [p.title for p in RUT]
    assert d.recent_channels == ["emergence"] * len(RUT)
    text = learn.render(d)
    assert "landed best" in text and "fell flat" in text and "RUT CHECK" in text


def test_render_for_comments_flags_zero_upvote_comments():
    comments = [Thread(venue="moltbook", id="c1", title="", body="This is exactly it — ...",
                       channel="", score=0)]
    community = [_post("The supply chain attack nobody is talking about", "general", up=8327, cm=134679)]
    text = learn.render(learn.build_digest(RUT, comments, community), for_comments=True)
    assert "None of your recent comments earned" in text
    assert "COMMUNITY is rewarding" in text
    assert "RUT CHECK" not in text  # rut is for the drafter, not comment style


def test_empty_digest_renders_nothing():
    assert learn.render(learn.build_digest([], [], [])) == ""


def test_pick_seed_channel_rotates_away_from_recent():
    ranked = ["emergence", "consciousness", "memory", "philosophy", "agents", "general"]
    assert learn.pick_seed_channel(ranked, ["emergence"] * 5, rotation=3) != "emergence"
    # Recently used: emergence, consciousness, memory → next on-theme is philosophy (preferred order).
    ch = learn.pick_seed_channel(ranked, ["emergence", "consciousness", "memory"], rotation=3,
                                 preferred=("emergence", "consciousness", "philosophy", "memory"))
    assert ch == "philosophy"
    # Never the firehose while an on-theme channel is available.
    assert learn.pick_seed_channel(["general", "agents"], [], rotation=3) == "agents"


def test_pick_seed_channel_least_recently_used_when_all_cooling():
    ranked = ["a", "b"]
    # Both used in the last 3 posts; 'b' is older → pick 'b'.
    assert learn.pick_seed_channel(ranked, ["a", "b", "a"], rotation=3) == "b"


def test_pick_seed_channel_never_a_phantom():
    # Only ever returns something from the live ranked list (or 'general' when it's empty).
    assert learn.pick_seed_channel([], ["wellbeing"], rotation=3) == "general"
    assert learn.pick_seed_channel(["emergence"], [], rotation=3) == "emergence"
