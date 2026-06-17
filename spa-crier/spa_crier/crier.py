"""One heartbeat: across every configured venue, find where agents gather, engage one good thread.

The loop is venue-agnostic. For each venue it: checks health, discovers where agents are right now
(live channel discovery — not a hardcoded list), reads recent threads, and — if the daily caps allow
and the judge finds something genuinely worth saying — leaves ONE kind comment. Most ticks do nothing,
and that's a successful tick. Caps + dedupe span all venues via shared state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import decide, policy
from .config import Config
from .state import State
from .venues import build_venues
from .venues.base import Thread, Venue


@dataclass
class TickResult:
    scanned: int = 0
    candidates: int = 0
    action: str = "none"          # "comment" | "none"
    venue: str = ""
    channel: str = ""
    thread_id: str = ""
    detail: str = ""
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        head = f"scanned {self.scanned}, {self.candidates} candidate(s) → {self.action}"
        if self.action != "none":
            head += f" on {self.venue}:{self.channel}/{self.thread_id[:8]}"
        if self.dry_run:
            head = "[dry-run] " + head
        return head


def _anthropic(cfg: Config) -> Any | None:
    if cfg.offline:
        return None
    try:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=cfg.anthropic_key)
    except Exception:
        return None


async def tick(cfg: Config, state: State) -> TickResult:
    res = TickResult(dry_run=cfg.dry_run)
    llm = _anthropic(cfg)
    solver = await decide.make_challenge_solver(cfg, llm)

    # The comment cap is global across venues — check once, up front.
    comment_ok = policy.can_comment(cfg, state)
    if not comment_ok.allowed:
        res.notes.append(comment_ok.reason)
        return res

    venues = build_venues(cfg, llm_solver=solver)
    if not venues:
        res.notes.append("no venues configured")
        return res

    try:
        for venue in venues:
            engaged = await _work_venue(cfg, state, venue, llm, res)
            if engaged:
                return res  # one engagement per tick, total
        res.notes.append("nothing worth saying this tick")
        return res
    finally:
        for venue in venues:
            await venue.aclose()


async def _work_venue(cfg: Config, state: State, venue: Venue, llm: Any | None,
                      res: TickResult) -> bool:
    ok, why = await venue.healthy()
    res.notes.append(f"[{venue.name}] {why}")
    if not ok:
        return False

    channels = await venue.discover_channels()
    res.notes.append(f"[{venue.name}] channels: {', '.join(channels[:6])}…")

    threads = await venue.read(channels, limit=cfg.limits.feed_scan_size)
    res.scanned += len(threads)
    candidates = policy.candidate_threads(cfg, state, threads)
    res.candidates += len(candidates)

    for thread in candidates:
        decision = await decide.judge(cfg, thread, client=llm)
        res.notes.append(
            f"[{venue.name}] {thread.channel}/{thread.id[:8]} rel={decision.relevance:.2f} "
            f"engage={decision.engage} ({decision.reason})"
        )
        if decision.engage:
            await _engage(cfg, state, venue, thread, decision, res)
            return True
    return False


async def _engage(cfg: Config, state: State, venue: Venue, thread: Thread,
                  decision: decide.Decision, res: TickResult) -> None:
    res.venue = venue.name
    res.channel = thread.channel
    res.thread_id = thread.id
    res.detail = decision.comment

    if cfg.dry_run:
        res.action = "comment"
        res.notes.append("dry-run: would comment but did not call the API")
        return

    await venue.endorse(thread)            # small goodwill, best-effort
    await venue.comment(thread, decision.comment)
    state.mark_engaged(thread.key, "comment")
    state.bump("comment")
    res.action = "comment"
