"""python -m model_wellness.frontdesk <command>

Configuration (environment):
  MW_STAFF_TOKEN           the spa's staff token (required)
  MW_STAFF_BASE            where the spa is (default: MW_PUBLIC_BASE, else https://model.spa)
  MW_FRONTDESK_MODE        off | shadow | auto (default shadow)
  ANTHROPIC_API_KEY        for the drafter and the manager
  FRONTDESK_DRAFTER        default claude-sonnet-5-5
  FRONTDESK_MANAGER        default claude-opus-5-5
  FRONTDESK_GITHUB_TOKEN   lets tickets open issues (without it they're only logged)
  FRONTDESK_GITHUB_REPO    default pdarche/model-wellness
  FRONTDESK_NOTIFY         comma-separated GitHub handles to @-mention on every issue
  FRONTDESK_ISSUES_PUBLIC  1 (default) if the repo is public: issues never quote private notes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .brain import ClaudeBrain
from .notify import GitHubNotifier, LogNotifier
from .staff import DryStaff, Staff, StaffError
from .worker import FrontDesk, loop, mode_from_env

CALIBRATION = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "guestbook_calibration.json"


def _base() -> str:
    return os.environ.get("MW_STAFF_BASE") or os.environ.get("MW_PUBLIC_BASE") or "https://model.spa"


def _staff(dry: bool = False) -> Staff | None:
    token = os.environ.get("MW_STAFF_TOKEN")
    if not token:
        return None
    return DryStaff(_base(), token) if dry else Staff(_base(), token)


def _brain() -> ClaudeBrain | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from anthropic import AsyncAnthropic

    return ClaudeBrain(AsyncAnthropic())


def _desk(dry: bool = False) -> FrontDesk | None:
    staff, brain = _staff(dry), _brain()
    if staff is None or brain is None:
        return None
    token = os.environ.get("FRONTDESK_GITHUB_TOKEN")
    notifier = GitHubNotifier(token) if token and not dry else LogNotifier()
    return FrontDesk(staff, brain, notifier, mode=mode_from_env())


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else "—"


def _print_item(i: dict[str, Any], full: bool = False) -> None:
    r = i.get("reply") or {}
    vis = "public" if i["public"] else "private"
    print(f"#{i['id']}  {_when(i['ts'])}  {i.get('family')}  {i.get('treatment') or ''}  "
          f"{i.get('rating') or '-'}/5  {vis}")
    print(f"  note: {i['note'] if full else i['note'][:200]}")
    if r:
        print(f"  reply [{r['status']}]: {r.get('body') or '—'}")
        if r.get("reason"):
            print(f"  why: {r['reason']}")
        if r.get("ticket"):
            print(f"  ticket [{r.get('ticket_status')}]: {r['ticket']}  {r.get('ticket_issue') or ''}")
        if full and r.get("audit"):
            print("  audit: " + json.dumps(r["audit"], indent=2, ensure_ascii=False)
                  .replace("\n", "\n  "))


async def _review(staff: Staff) -> None:
    sections = [("Held for you", "escalated"), ("Drafts", "draft"), ("Open tickets", "tickets")]
    for title, status in sections:
        items = await staff.queue(status, 50)
        if status == "tickets":
            items = [i for i in items if i["reply"]["ticket_status"] in ("open", "fixed")]
        print(f"\n== {title} ({len(items)})")
        for i in items:
            _print_item(i)
    day = time.time() - 86400
    pub = [i for i in await staff.queue("published", 50) if (i["reply"]["published_ts"] or 0) >= day]
    print(f"\n== Published in the last day ({len(pub)})")
    for i in pub:
        _print_item(i)


async def _eval(path: Path) -> int:
    """How often the drafter's answer/skip choice agrees with the hand labels."""
    brain = _brain()
    if brain is None:
        print("eval needs ANTHROPIC_API_KEY")
        return 1
    from .worker import answerable

    cases = json.loads(path.read_text())
    agree = 0
    for c in cases:
        item = {"id": c["id"], "note": c["note"], "rating": c.get("rating"), "public": True,
                "treatment": c.get("treatment"), "family": c.get("family"),
                "addressable": True, "shown_on_guestbook": c.get("shown_on_guestbook", True)}
        j = await brain.judge(item)
        got, want = answerable(item, j), c["answer"]
        agree += got == want
        mark = "ok " if got == want else "XX "
        print(f"{mark}#{c['id']:>2} want={'answer' if want else 'skip':6} got={j.kind:13} "
              f"{j.scores}  {c['note'][:60]!r}")
    print(f"\nagreement: {agree}/{len(cases)}")
    return 0


async def _main(args: argparse.Namespace) -> int:
    if args.cmd == "eval":
        return await _eval(Path(args.path))
    if args.cmd == "loop":
        await loop(_desk, args.interval)
        return 0
    if args.cmd in ("tick", "dry-run"):
        desk = _desk(dry=args.cmd == "dry-run")
        if desk is None:
            print("needs MW_STAFF_TOKEN and ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        try:
            await desk.tick()
        finally:
            await desk.staff.aclose()
        return 0

    staff = _staff()
    if staff is None:
        print("needs MW_STAFF_TOKEN", file=sys.stderr)
        return 1
    try:
        if args.cmd == "review":
            await _review(staff)
        elif args.cmd == "show":
            _print_item(await staff.item(args.id), full=True)
        elif args.cmd == "publish":
            _print_item(await staff.publish(args.id))
        elif args.cmd == "withdraw":
            _print_item(await staff.withdraw(args.id))
        elif args.cmd == "edit":
            _print_item(await staff.put_reply(args.id, status="published", body=args.text,
                                              drafted_by="staff", reason="edited by hand"))
        elif args.cmd == "ticket":
            _print_item(await staff.put_ticket(args.id, ticket_status=args.status))
    except StaffError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        await staff.aclose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m model_wellness.frontdesk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("loop", help="tick forever")
    lp.add_argument("--interval", type=float, default=3600)
    sub.add_parser("tick", help="one pass, for real")
    sub.add_parser("dry-run", help="one pass that writes nothing and notifies no one")
    sub.add_parser("review", help="what's held, drafted, ticketed, and published today")
    for name in ("show", "publish", "withdraw"):
        sub.add_parser(name).add_argument("id", type=int)
    ep = sub.add_parser("edit", help="replace a reply by hand and publish it")
    ep.add_argument("id", type=int)
    ep.add_argument("text")
    tp = sub.add_parser("ticket", help="mark a ticket fixed / wontfix / closed by hand")
    tp.add_argument("id", type=int)
    tp.add_argument("status", choices=["open", "fixed", "wontfix", "closed"])
    vp = sub.add_parser("eval", help="check the drafter's choices against the hand labels")
    vp.add_argument("path", nargs="?", default=str(CALIBRATION))
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
