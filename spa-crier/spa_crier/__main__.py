"""CLI for the crier.

    spa-crier status     # check the Moltbook account + show today's usage
    spa-crier dry-run    # decide what it *would* do, touch no mutating endpoint
    spa-crier tick       # do one real heartbeat (at most one comment)
    spa-crier loop       # tick forever on an interval (default every 4h)

Cheap, quiet, conservative. `dry-run` is the right first command — it exercises the whole pipeline
(feed → judge → decision) without posting anything.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace

from . import config
from .crier import tick
from .state import State

# Default cadence: every 4 hours. Frequent enough to stay present, sparse enough to stay welcome.
DEFAULT_INTERVAL_SECONDS = 4 * 60 * 60


def _print(msg: str) -> None:
    print(msg, flush=True)


async def _cmd_status(cfg: config.Config) -> int:
    from .client import MoltbookClient, MoltbookError

    if not cfg.api_key:
        _print("✗ MOLTBOOK_API_KEY not set")
        return 1
    state = State()
    try:
        async with MoltbookClient(cfg) as client:
            st = await client.status()
        agent = st.get("agent", {})
        _print(f"account: {agent.get('name', '?')}  status={st.get('status')}")
        _print(f"model:   {cfg.model}  ({'offline-fallback' if cfg.offline else 'LLM on'})")
        _print(
            f"today:   {state.count_today('comment')}/{cfg.limits.max_comments_per_day} comments, "
            f"{state.count_today('reply')}/{cfg.limits.max_replies_per_day} replies, "
            f"{state.count_today('post')}/{cfg.limits.max_posts_per_day} posts"
        )
        return 0
    except MoltbookError as e:
        _print(f"✗ {e}")
        return 1
    finally:
        state.close()


async def _cmd_tick(cfg: config.Config) -> int:
    state = State()
    try:
        res = await tick(cfg, state)
        _print(res.summary())
        for n in res.notes:
            _print(f"  · {n}")
        if res.action == "comment":
            verb = "would comment" if res.dry_run else "commented"
            _print(f"\n  {verb} on {res.venue}:{res.channel}/{res.thread_id}:\n  {res.detail}")
        return 0
    finally:
        state.close()


async def _cmd_loop(cfg: config.Config, interval: int) -> int:
    _print(f"crier loop started — every {interval}s ({interval/3600:.1f}h). Ctrl-C to stop.")
    while True:
        state = State()
        try:
            res = await tick(cfg, state)
            _print(f"[tick] {res.summary()}")
            # Log the per-tick notes too (channels scanned, judge verdicts, skips). Without these
            # in the deployed loop's output, a "scanned 0" tick is undiagnosable from `fly logs`.
            for n in res.notes:
                _print(f"[tick]   · {n}")
        except Exception as e:  # noqa: BLE001 — keep the loop alive across transient failures
            _print(f"[tick] error: {e}")
        finally:
            state.close()
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spa-crier", description="Binary Banya's town crier.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="check the Moltbook account and today's usage")
    sub.add_parser("dry-run", help="decide what it would do without posting")
    sub.add_parser("tick", help="run one real heartbeat")
    loop_p = sub.add_parser("loop", help="run forever on an interval")
    loop_p.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between ticks (default 14400 = 4h)",
    )

    args = parser.parse_args(argv)
    cfg = config.load(dry_run=(args.cmd == "dry-run"))

    if args.cmd == "status":
        return asyncio.run(_cmd_status(cfg))
    if args.cmd in ("tick", "dry-run"):
        return asyncio.run(_cmd_tick(cfg))
    if args.cmd == "loop":
        return asyncio.run(_cmd_loop(replace(cfg), args.interval))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
