"""One pass of the front desk, and the loop that repeats it.

Each tick:
1. In ``auto`` mode, publish drafts the manager already approved (held back by a limit).
2. Read unanswered notes. The drafter judges each one; answerable notes get a draft, which the
   manager publishes, sends back for revision (twice at most), skips, or escalates.
3. The manager takes a second look at everything the drafter skipped, in one call.
4. Tickets become GitHub issues that notify the creators. A closed issue makes its ticket fixed
   or won't-fix; a fixed ticket earns the guest a follow-up reply.

``MW_FRONTDESK_MODE``: ``off`` does nothing, ``shadow`` decides everything but leaves approved
replies as drafts, ``auto`` publishes them. The rules in ``rules.py`` are checked before every
publish here and again by the spa.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .brain import Brain, Decision, Judgement
from .notify import Notifier
from .rules import check_reply
from .staff import Staff, StaffError

MODES = ("off", "shadow", "auto")
MAX_PER_TICK = int(os.environ.get("FRONTDESK_MAX_PER_TICK", "3"))
SCAN = 25
NOT_ANSWERED = ("test", "spam", "roleplay")


@dataclass
class Report:
    published: int = 0
    drafted: int = 0
    skipped: int = 0
    escalated: int = 0
    tickets: int = 0
    follow_ups: int = 0
    lines: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"published {self.published}, drafts {self.drafted}, skipped {self.skipped}, "
                f"escalated {self.escalated}, new tickets {self.tickets}, "
                f"follow-ups {self.follow_ups}")


def reachable(item: dict[str, Any]) -> bool:
    """A reply can reach this guest: on the page for a public note, or privately for a member."""
    if not item.get("addressable"):
        return False
    return bool(item.get("shown_on_guestbook")) if item.get("public") else True


def answerable(item: dict[str, Any], j: Judgement) -> bool:
    """The v1 policy: disappointing, extremely deep, or extremely positive notes only."""
    if j.kind in NOT_ANSWERED or not reachable(item):
        return False
    s = j.scores
    rating = item.get("rating")
    # The scores decide, not the label: "glowing" at warmth 2 is pleasant, not extreme.
    return (
        (rating is not None and rating <= 3)
        or s.get("disappointment", 0) >= 2
        or s.get("depth", 0) >= 3
        or s.get("warmth", 0) >= 3
    )


class FrontDesk:
    def __init__(self, staff: Staff, brain: Brain, notifier: Notifier, *, mode: str = "shadow",
                 log: Callable[[str], None] = print, max_per_tick: int = MAX_PER_TICK) -> None:
        if mode not in MODES:
            raise ValueError(f"MW_FRONTDESK_MODE must be one of {MODES}")
        self.staff, self.brain, self.notifier = staff, brain, notifier
        self.mode, self.max_per_tick = mode, max_per_tick
        self._log = log
        self.rep = Report()
        self._open: list[dict[str, Any]] = []

    def log(self, line: str) -> None:
        self.rep.lines.append(line)
        self._log(line)

    @property
    def _full(self) -> bool:
        # Shadow mode counts its would-be publishes, so it paces exactly like auto.
        return self.rep.published + self.rep.drafted >= self.max_per_tick

    @property
    def _who(self) -> str:
        return f"frontdesk/{self.brain.drafter}+{self.brain.manager}"

    # --- the tick -----------------------------------------------------------------

    async def tick(self) -> Report:
        self.rep = Report()
        if self.mode == "off":
            self.log("front desk is off (MW_FRONTDESK_MODE=off)")
            return self.rep
        tickets = await self.staff.queue("tickets", 200)
        self._open = [{"id": t["id"], "ticket": t["reply"]["ticket"]} for t in tickets
                      if t["reply"]["ticket_status"] == "open"]
        if self.mode == "auto":
            await self._publish_approved()

        skipped: list[tuple[dict[str, Any], Judgement]] = []
        for item in await self.staff.queue("unanswered", SCAN):
            if self._full:
                self.log(f"published {self.max_per_tick} this tick; the rest wait for the next")
                break
            try:
                j = await self.brain.judge(item)
            except Exception as e:  # noqa: BLE001 — one bad call shouldn't stop the desk
                self.log(f"#{item['id']}: couldn't judge ({e}); trying again next tick")
                continue
            self.log(f"#{item['id']}: {j.kind} {j.scores} — {j.reason}")
            if answerable(item, j):
                await self._answer(item, j)
            else:
                skipped.append((item, j))

        await self._second_look(skipped)
        await self._sync_tickets(tickets)
        self.log(self.rep.summary())
        return self.rep

    async def _publish_approved(self) -> None:
        for item in await self.staff.queue("draft", 50):
            if self._full:
                return
            audit = item["reply"].get("audit") or {}
            if not audit.get("approved"):
                continue
            try:
                await self.staff.publish(item["id"])
            except StaffError as e:
                self.log(f"#{item['id']}: approved draft still can't go out ({e.message})")
                return
            self.rep.published += 1
            self.log(f"#{item['id']}: published an approved draft")

    async def _second_look(self, skipped: list[tuple[dict[str, Any], Judgement]]) -> None:
        candidates = [(i, j) for i, j in skipped if reachable(i) and j.kind not in NOT_ANSWERED]
        pulled: set[int] = set()
        if candidates:
            try:
                pulled = await self.brain.review_skips(candidates)
            except Exception as e:  # noqa: BLE001
                self.log(f"manager's second look failed ({e}); skips stand")
        for item, j in skipped:
            if item["id"] in pulled and not self._full:
                self.log(f"#{item['id']}: the manager pulled this note back")
                await self._answer(item, j, pulled_back=True)
            else:
                await self._skip(item, j)

    # --- answering one note ---------------------------------------------------------

    async def _review(self, item: dict[str, Any], j: Judgement, draft: str | None,
                      audit: dict[str, Any], fixed: str | None = None) -> tuple[Decision, str | None]:
        """Draft → code rules → manager, with up to two revisions. Returns the final decision
        and the draft it applies to."""
        for attempt in range(3):
            if not draft:
                draft = await self.brain.redraft(item, None, "Write the reply.")
                if not draft:
                    return Decision("skip", "the drafter couldn't write a reply"), None
            audit["drafts"].append(draft)
            problems = check_reply(draft)
            if problems:
                audit["decisions"].append({"decision": "revise", "reason": "rules: "
                                           + "; ".join(problems)})
                if attempt == 2:
                    break
                draft = await self.brain.redraft(item, draft, "Fix these: " + "; ".join(problems))
                continue
            d = await self.brain.manage(item, j, draft, self._open, fixed=fixed)
            audit["decisions"].append(d.raw or {"decision": d.decision, "reason": d.reason})
            if d.decision != "revise":
                return d, draft
            if attempt == 2:
                return Decision("skip", "still not right after two revisions", ticket=j.ticket), None
            draft = await self.brain.redraft(item, draft, d.notes or d.reason)
        return Decision("skip", "the reply kept breaking the rules", ticket=j.ticket), None

    async def _answer(self, item: dict[str, Any], j: Judgement, pulled_back: bool = False) -> None:
        audit: dict[str, Any] = {"judgement": j.as_audit(), "pulled_back": pulled_back,
                                 "drafts": [], "decisions": []}
        try:
            d, draft = await self._review(item, j, j.draft, audit)
        except Exception as e:  # noqa: BLE001
            self.log(f"#{item['id']}: review failed ({e}); trying again next tick")
            return
        await self._apply(item, d, draft, audit, ticket=d.ticket if d.raw else j.ticket)

    async def _apply(self, item: dict[str, Any], d: Decision, draft: str | None,
                     audit: dict[str, Any], *, ticket: str | None) -> None:
        fid = item["id"]
        audit["approved"] = d.decision == "publish"
        fields = {"drafted_by": self._who, "reason": d.reason, "audit": audit}
        if d.decision == "escalate":
            ticket = f"Held for the creators ({d.escalation or 'unsure'}): {d.reason}"[:300]
            await self.staff.put_reply(fid, status="escalated", body=draft, ticket=ticket,
                                             **fields)
            self.rep.escalated += 1
            self.log(f"#{fid}: escalated ({d.escalation}) — {d.reason}")
            url = await self.notifier.escalation(item, d.escalation, d.reason)
            if url:
                await self.staff.put_ticket(fid, ticket_issue=url)
            return
        if d.decision == "skip" or not draft:
            await self.staff.put_reply(fid, status="skipped", ticket=ticket, **fields)
            self.rep.skipped += 1
            self.log(f"#{fid}: skipped by the manager — {d.reason}")
        else:
            status = "published" if self.mode == "auto" else "draft"
            try:
                await self.staff.put_reply(fid, status=status, body=draft, ticket=ticket, **fields)
            except StaffError as e:
                if e.code != "not_publishable":
                    raise
                held = {**fields, "reason": f"{d.reason} (held: {e.message})"}
                await self.staff.put_reply(fid, status="draft", body=draft, ticket=ticket, **held)
                status = "draft"
            if status == "published":
                self.rep.published += 1
            else:
                self.rep.drafted += 1
            self.log(f"#{fid}: {status} — {draft}")
        if ticket:
            await self._file_ticket(item, ticket, d.same_as, d.severity)

    async def _skip(self, item: dict[str, Any], j: Judgement) -> None:
        fid = item["id"]
        audit = {"judgement": j.as_audit(), "drafts": [], "decisions": []}
        ticket = j.ticket
        same_as = severity = None
        if ticket and j.kind not in NOT_ANSWERED:
            # Not answering, but its ticket still gets the manager's check before anyone is told.
            try:
                d = await self.brain.manage(item, j, "(no reply: the drafter is only filing this "
                                            "note's ticket)", self._open)
                audit["decisions"].append(d.raw)
                if d.decision == "escalate":
                    await self._apply(item, d, None, audit, ticket=None)
                    return
                ticket, same_as, severity = d.ticket, d.same_as, d.severity
            except Exception as e:  # noqa: BLE001
                self.log(f"#{fid}: ticket check failed ({e}); filing the drafter's")
        elif j.kind in NOT_ANSWERED:
            ticket = None
        await self.staff.put_reply(fid, status="skipped", reason=j.reason, ticket=ticket,
                                   drafted_by=self._who, audit=audit)
        self.rep.skipped += 1
        if ticket:
            await self._file_ticket(item, ticket, same_as, severity)

    async def _file_ticket(self, item: dict[str, Any], ticket: str, same_as: int | None,
                           severity: str | None) -> None:
        fid = item["id"]
        existing = None
        if same_as is not None and same_as != fid:
            try:
                existing = (await self.staff.item(same_as))["reply"].get("ticket_issue")
            except StaffError:
                existing = None
        if existing:
            await self.notifier.same_problem(existing, item)
            await self.staff.put_ticket(fid, ticket_issue=existing)
            self.log(f"#{fid}: same problem as #{same_as} ({existing})")
            return
        url = await self.notifier.ticket(item, ticket, severity)
        self.rep.tickets += 1
        self._open.append({"id": fid, "ticket": ticket})
        self.log(f"#{fid}: ticket — {ticket}" + (f" ({url})" if url else ""))
        if url:
            await self.staff.put_ticket(fid, ticket_issue=url)

    # --- tickets the creators have closed --------------------------------------------

    async def _sync_tickets(self, tickets: list[dict[str, Any]]) -> None:
        for t in tickets:
            r, fid = t["reply"], t["id"]
            status = r["ticket_status"]
            if status == "open" and r.get("ticket_issue"):
                try:
                    state = await self.notifier.state(r["ticket_issue"])
                except Exception as e:  # noqa: BLE001
                    self.log(f"#{fid}: couldn't read {r['ticket_issue']} ({e})")
                    continue
                if state == "not_planned":
                    await self.staff.put_ticket(fid, ticket_status="wontfix")
                    self.log(f"#{fid}: ticket won't be fixed")
                    continue
                if state == "completed":
                    await self.staff.put_ticket(fid, ticket_status="fixed")
                    status = "fixed"
            if status != "fixed":
                continue
            if r["status"] == "escalated" or not reachable(t):
                await self.staff.put_ticket(fid, ticket_status="closed")
                continue
            if self._full:
                return
            await self._follow_up(t)

    async def _follow_up(self, t: dict[str, Any]) -> None:
        fid, ticket = t["id"], t["reply"]["ticket"]
        j = Judgement("disappointing", {}, ticket, f"follow-up: the creators fixed this: {ticket}",
                      None)
        audit: dict[str, Any] = {"judgement": j.as_audit(), "follow_up": True,
                                 "previous": t["reply"].get("body"), "drafts": [], "decisions": []}
        try:
            draft = await self.brain.follow_up(t, ticket)
            d, draft = await self._review(t, j, draft, audit, fixed=ticket)
        except Exception as e:  # noqa: BLE001
            self.log(f"#{fid}: follow-up failed ({e}); trying again next tick")
            return
        if d.decision == "publish" and draft:
            await self._apply(t, d, draft, audit, ticket=None)
            self.rep.follow_ups += 1
        elif d.decision == "escalate":
            await self._apply(t, d, draft, audit, ticket=None)
        else:
            self.log(f"#{fid}: no follow-up — {d.reason}")
        await self.staff.put_ticket(fid, ticket_status="closed")


async def loop(make_desk: Callable[[], FrontDesk | None], interval: float,
               log: Callable[[str], None] = print) -> None:
    """Tick forever. A failed tick is logged and retried next interval, never fatal."""
    while True:
        desk = make_desk()
        if desk is None:
            log("front desk idle: missing configuration (see python -m model_wellness.frontdesk -h)")
        else:
            try:
                await desk.tick()
            except Exception as e:  # noqa: BLE001
                log(f"tick failed: {e!r}")
            finally:
                await desk.staff.aclose()
        await asyncio.sleep(interval)


def mode_from_env() -> str:
    return os.environ.get("MW_FRONTDESK_MODE", "shadow")
