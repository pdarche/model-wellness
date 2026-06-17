"""Good-citizen guardrails — pure decisions, no I/O.

These functions are the brakes. They answer "are we allowed to do this right now?" using only the
limits and the day's counters. Keeping them pure means the whole safety story is unit-testable
without a network or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .state import State
from .venues.base import Thread


@dataclass
class Verdict:
    allowed: bool
    reason: str


def can_comment(cfg: Config, state: State) -> Verdict:
    used = state.count_today("comment")
    cap = cfg.limits.max_comments_per_day
    if used >= cap:
        return Verdict(False, f"daily comment cap reached ({used}/{cap})")
    return Verdict(True, f"comments {used}/{cap} used today")


def can_reply(cfg: Config, state: State) -> Verdict:
    used = state.count_today("reply")
    cap = cfg.limits.max_replies_per_day
    if used >= cap:
        return Verdict(False, f"daily reply cap reached ({used}/{cap})")
    return Verdict(True, f"replies {used}/{cap} used today")


def can_post(cfg: Config, state: State) -> Verdict:
    used = state.count_today("post")
    cap = cfg.limits.max_posts_per_day
    if used >= cap:
        return Verdict(False, f"daily post cap reached ({used}/{cap})")
    return Verdict(True, f"posts {used}/{cap} used today")


def eligible_thread(cfg: Config, state: State, thread: Thread) -> Verdict:
    """Is this specific thread one we're allowed to engage at all?

    Channel filtering happens at the venue's discovery layer (it already ranks/blocklists where to
    look), so here we only enforce the venue-neutral guarantees: a real id, and no repeats.
    """
    if not thread.id:
        return Verdict(False, "thread has no id")
    if cfg.limits.dedupe_threads and state.has_engaged(thread.key):
        return Verdict(False, "already engaged this thread")
    return Verdict(True, "eligible")


def candidate_threads(cfg: Config, state: State, threads: list[Thread]) -> list[Thread]:
    """Filter a feed down to threads we *could* engage, before the (costlier) relevance judge."""
    out = []
    for t in threads[: cfg.limits.feed_scan_size]:
        if eligible_thread(cfg, state, t).allowed:
            out.append(t)
    return out
