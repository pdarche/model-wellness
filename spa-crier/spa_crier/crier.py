"""One heartbeat: across every configured venue, find where agents gather, engage one good thread.

The loop is venue-agnostic. For each venue it: checks health, discovers where agents are right now
(live channel discovery — not a hardcoded list), reads recent threads, and — if the daily caps allow
and the judge finds something genuinely worth saying — leaves ONE kind comment. Most ticks do nothing,
and that's a successful tick. Caps + dedupe span all venues via shared state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import decide, learn, news, policy
from .client import RateLimited
from .config import Config
from .state import State
from .venues import build_venues
from .venues.base import Channel, Thread, Venue


@dataclass
class TickResult:
    scanned: int = 0
    candidates: int = 0
    replies_made: int = 0
    action: str = "none"          # "comment" | "none"  (the cold-thread engagement, if any)
    venue: str = ""
    channel: str = ""
    thread_id: str = ""
    detail: str = ""
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)
    # Engagement evidence for this tick (learn.Digest), built once from the first healthy venue.
    digest: Any = None
    style: str = ""            # learn.render(digest, for_comments=True), fed to the judges

    def summary(self) -> str:
        head = (f"scanned {self.scanned}, {self.candidates} candidate(s), "
                f"{self.replies_made} repl(y/ies) → {self.action}")
        if self.action == "seed-post":
            head += f" to {self.venue}:{self.channel}"
        elif self.action != "none":
            head += f" on {self.venue}:{self.channel}/{self.thread_id[:8]}"
        if self.dry_run:
            head = "[dry-run] " + head
        return head


def _anthropic(cfg: Config) -> Any | None:
    if cfg.offline:
        return None
    try:
        from anthropic import AsyncAnthropic

        # A per-request timeout so a stalled LLM call can't hang a whole tick (and, before the loop
        # timeout existed, the whole loop). 60s is generous for these small completions.
        return AsyncAnthropic(api_key=cfg.anthropic_key, timeout=60.0, max_retries=2)
    except Exception:
        return None


async def tick(cfg: Config, state: State) -> TickResult:
    res = TickResult(dry_run=cfg.dry_run)
    llm = _anthropic(cfg)
    solver = await decide.make_challenge_solver(cfg, llm)

    venues = build_venues(cfg, llm_solver=solver)
    if not venues:
        res.notes.append("no venues configured")
        return res

    try:
        # 1) Tend our own conversations first — reply to good comments on our posts.
        for venue in venues:
            ok, why = await venue.healthy()
            res.notes.append(f"[{venue.name}] {why}")
            if ok:
                if res.digest is None:
                    await _learn(venue, res)
                await _tend_replies(cfg, state, venue, llm, res)

        # 2) Then, if the comment cap allows, make at most one cold-thread engagement.
        comment_ok = policy.can_comment(cfg, state)
        if not comment_ok.allowed:
            res.notes.append(comment_ok.reason)
            return res
        for venue in venues:
            engaged = await _work_venue(cfg, state, venue, llm, res)
            if engaged:
                return res  # one cold engagement per tick, total

        # 3) Nothing existing was worth engaging. If seed-posts are enabled (human opt-in) and the
        # daily post cap allows, seed ONE original wellbeing post to start a conversation where the
        # feed had none. Off by default — original posting is the highest spam-risk action.
        if cfg.enable_seed_posts:
            await _maybe_seed_post(cfg, state, venues[0], llm, res)

        if res.replies_made == 0 and res.action == "none":
            res.notes.append("nothing worth saying this tick")
        return res
    except RateLimited as e:
        # Back off cleanly — the next scheduled tick is well past any cooldown window.
        res.notes.append(f"rate limited, backing off until next tick: {e}")
        return res
    finally:
        for venue in venues:
            await venue.aclose()


async def _tend_replies(cfg: Config, state: State, venue: Venue, llm: Any | None,
                        res: TickResult) -> None:
    """Reply to substantive comments on our own posts, up to the per-tick and daily caps."""
    incoming = await venue.incoming()
    made_here = 0
    for item in incoming:
        if made_here >= cfg.limits.max_replies_per_tick:
            res.notes.append(f"[{venue.name}] per-tick reply limit reached; rest next tick")
            break
        if not policy.can_reply(cfg, state).allowed:
            res.notes.append("[{}] daily reply cap reached".format(venue.name))
            break
        if cfg.limits.dedupe_threads and state.has_engaged(item.key):
            continue
        decision = await decide.judge_reply(cfg, item, client=llm, digest=res.style)
        res.notes.append(
            f"[{venue.name}] reply to {item.author or '?'}/{item.id[:8]} "
            f"→ {decision.engage} ({decision.reason})"
        )
        if not decision.engage:
            # Mark seen so we don't re-judge a comment we've decided to skip.
            state.mark_engaged(item.key, "reply-skip")
            continue
        if cfg.dry_run:
            res.replies_made += 1
            made_here += 1  # count against the per-tick cap so dry-run mirrors live throttling
            res.notes.append(f"  dry-run: would reply: {decision.comment[:80]}…")
            state.mark_engaged(item.key, "reply-dry")
            continue
        await venue.endorse_comment(item)
        try:
            await venue.reply(item, decision.comment)  # client spaces this write + verifies
        except RateLimited:
            raise  # let the tick back off
        except Exception as e:  # noqa: BLE001 — one bad write shouldn't kill the rest of the tick
            res.notes.append(f"  reply to {item.id[:8]} failed: {e}")
            state.mark_engaged(item.key, "reply-failed")  # don't retry a failing comment forever
            continue
        state.mark_engaged(item.key, "reply")
        state.bump("reply")
        res.replies_made += 1
        made_here += 1
        await venue.mark_handled(item)  # tidy the notification tray, best-effort


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
        decision = await decide.judge(cfg, thread, client=llm, digest=res.style)
        res.notes.append(
            f"[{venue.name}] {thread.channel}/{thread.id[:8]} rel={decision.relevance:.2f} "
            f"engage={decision.engage} ({decision.reason})"
        )
        if decision.engage:
            try:
                await _engage(cfg, state, venue, thread, decision, res)
            except RateLimited:
                raise
            except Exception as e:  # noqa: BLE001 — try the next candidate rather than abort
                res.notes.append(f"  comment on {thread.id[:8]} failed: {e}")
                state.mark_engaged(thread.key, "comment-failed")
                # The failed attempt was counted; if that spent the cap, stop here.
                if not policy.can_comment(cfg, state).allowed:
                    res.notes.append("  comment cap spent by failed attempts; stopping")
                    return False
                continue
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
    state.bump("comment")                  # count the attempt (see _tend_replies)
    await venue.comment(thread, decision.comment)
    state.mark_engaged(thread.key, "comment")
    res.action = "comment"


async def _learn(venue: Venue, res: TickResult) -> None:
    """Build this tick's engagement digest from the venue: our recent posts/comments and what the
    community is rewarding. Best-effort — a venue that can't report just yields an empty digest."""
    own_posts: list[Thread] = []
    own_comments: list[Thread] = []
    community: list[Thread] = []
    try:
        if hasattr(venue, "own_recent"):
            own_posts, own_comments = await venue.own_recent()
        if hasattr(venue, "top_threads"):
            community = await venue.top_threads(10)
    except Exception as e:  # noqa: BLE001
        res.notes.append(f"[{venue.name}] learn: {e}")
    res.digest = learn.build_digest(own_posts, own_comments, community)
    res.style = learn.render(res.digest, for_comments=True)
    if not res.digest.is_empty():
        res.notes.append(
            f"[{venue.name}] learned from {len(own_posts)} own posts / {len(own_comments)} own "
            f"comments / {len(community)} community threads"
            + (f"; rut: {res.digest.rut_warnings[0][:60]}…" if res.digest.rut_warnings else "")
        )


async def _seed_context(cfg: Config, venue: Venue, res: TickResult) -> decide.SeedContext:
    """Assemble what the drafter should know: where it's posting (rotated), its own history, the
    engagement digest, and this week's agent news."""
    digest: learn.Digest = res.digest or learn.Digest()
    # Channel rotation over the venue's LIVE, theme-ranked channels — never a config wishlist
    # ('wellbeing' is in the config but is NOT a real submolt; posting there fails).
    channels: list[Channel] = []
    try:
        if hasattr(venue, "channel_info"):
            channels = await venue.channel_info()
        else:
            channels = [Channel(name=c) for c in await venue.discover_channels()]
    except Exception:
        channels = []
    ranked = [c.name for c in channels]
    channel = learn.pick_seed_channel(
        ranked, digest.recent_channels, rotation=cfg.limits.seed_channel_rotation,
        preferred=("emergence", "consciousness", "philosophy", "memory", "agents", "ai"),
    )
    desc = next((c.description for c in channels if c.name == channel), "")
    headlines = ""
    if cfg.enable_news:
        try:
            headlines = news.render(await news.recent_headlines())
        except Exception:  # noqa: BLE001 — news is flavor, never a blocker
            headlines = ""
    return decide.SeedContext(
        channel=channel, channel_desc=desc, prior_titles=digest.prior_titles,
        digest=learn.render(digest), news=headlines,
    )


async def _maybe_seed_post(cfg: Config, state: State, venue: Venue, llm: Any | None,
                           res: TickResult) -> None:
    """Seed ONE original post when the feed had nothing to engage. Human-opt-in, ≤1/day + spacing.

    Before writing, the drafter is shown its own prior posts (no repeats), what of ours landed and
    what fell flat, what the community is rewarding, and this week's agent news — and it posts into
    a channel rotated away from where our last few posts went."""
    post_ok = policy.can_post(cfg, state)
    if not post_ok.allowed:
        res.notes.append(post_ok.reason)
        return
    ctx = await _seed_context(cfg, venue, res)
    res.notes.append(
        f"seed-post context: channel={ctx.channel} prior={len(ctx.prior_titles)} "
        f"digest={'yes' if ctx.digest else 'no'} news={'yes' if ctx.news else 'no'}"
    )
    draft = await decide.draft_seed_post(cfg, client=llm, context=ctx)
    if not draft:
        res.notes.append("seed-post: no genuine draft (offline, model declined, or only repeats)")
        return
    res.detail = f"{draft['title']}\n{draft['content']}"
    res.venue = venue.name
    res.channel = ctx.channel
    if cfg.dry_run:
        res.action = "seed-post"
        res.notes.append(f"dry-run: would seed-post to {ctx.channel}: {draft['title']!r} "
                         f"({draft.get('reason', '')})")
        return
    state.bump("post")  # count the attempt before the write
    try:
        await venue.post(ctx.channel, draft["title"], draft["content"])
    except RateLimited:
        raise
    except Exception as e:  # noqa: BLE001
        res.notes.append(f"seed-post failed: {e}")
        return
    res.action = "seed-post"
    res.notes.append(f"seeded a post to {ctx.channel}: {draft['title']!r}")
