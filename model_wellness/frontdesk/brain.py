"""The two models behind the front desk: Sonnet drafts, Opus manages.

``Brain`` is the interface the worker uses; ``ClaudeBrain`` is the real one. Tests swap in a
scripted brain, so the worker's decisions can be checked without an API key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..llm import extract_json
from . import prompts

DRAFTER = os.environ.get("FRONTDESK_DRAFTER", "claude-sonnet-5-5")
MANAGER = os.environ.get("FRONTDESK_MANAGER", "claude-opus-5-5")

KINDS_ANSWERED = ("disappointing", "deep", "glowing")
REFUSED = object()  # the model declined to answer (stop_reason "refusal")


@dataclass
class Judgement:
    kind: str
    scores: dict[str, int]
    ticket: str | None
    reason: str
    draft: str | None

    def as_audit(self) -> dict[str, Any]:
        return {"kind": self.kind, "scores": self.scores, "ticket": self.ticket,
                "reason": self.reason}


@dataclass
class Decision:
    decision: str            # publish | revise | skip | escalate
    reason: str
    notes: str | None = None
    escalation: str | None = None
    ticket: str | None = None
    same_as: int | None = None
    severity: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Brain(Protocol):
    drafter: str
    manager: str

    async def judge(self, item: dict[str, Any]) -> Judgement: ...

    async def redraft(self, item: dict[str, Any], draft: str | None, feedback: str) -> str | None: ...

    async def follow_up(self, item: dict[str, Any], ticket: str) -> str | None: ...

    async def manage(self, item: dict[str, Any], j: Judgement, draft: str,
                     open_tickets: list[dict[str, Any]], fixed: str | None = None) -> Decision: ...

    async def review_skips(self, skipped: list[tuple[dict[str, Any], Judgement]]) -> set[int]: ...


def _int(v: Any) -> int:
    try:
        return max(0, min(3, int(v)))
    except (TypeError, ValueError):
        return 0


def _str(v: Any) -> str | None:
    if v is None or str(v).strip() in ("", "null", "None"):
        return None
    return str(v).strip()


def parse_judgement(data: Any) -> Judgement:
    if not isinstance(data, dict):
        return Judgement("routine", {}, None, "the drafter's answer couldn't be read", None)
    scores = data.get("scores") or {}
    return Judgement(
        kind=str(data.get("kind") or "routine"),
        scores={k: _int(scores.get(k)) for k in ("disappointment", "depth", "warmth")},
        ticket=_str(data.get("ticket")),
        reason=str(data.get("reason") or ""),
        draft=_str(data.get("draft")),
    )


def parse_decision(data: Any) -> Decision:
    if not isinstance(data, dict) or data.get("decision") not in (
        "publish", "revise", "skip", "escalate"
    ):
        # A manager answer we can't read is a hold, never a publish.
        return Decision("escalate", "the manager's answer couldn't be read", escalation="unsure",
                        raw=data if isinstance(data, dict) else {})
    same_as = data.get("same_as")
    return Decision(
        decision=data["decision"],
        reason=str(data.get("reason") or ""),
        notes=_str(data.get("notes")),
        escalation=_str(data.get("escalation")),
        ticket=_str(data.get("ticket")),
        same_as=same_as if isinstance(same_as, int) else None,
        severity=data.get("severity") if data.get("severity") in ("low", "medium", "high") else None,
        raw=data,
    )


class ClaudeBrain:
    def __init__(self, client: Any, drafter: str = DRAFTER, manager: str = MANAGER) -> None:
        self.client = client
        self.drafter = drafter
        self.manager = manager

    async def _ask(self, model: str, system: str, user: str, *, think: bool = False) -> Any:
        """The parsed JSON answer, or REFUSED if the model declined to answer at all."""
        kwargs: dict[str, Any] = {}
        if think:
            kwargs["thinking"] = {"type": "adaptive"}
        msg = await self.client.messages.create(
            model=model, max_tokens=4000 if think else 1200, system=system,
            messages=[{"role": "user", "content": user}], **kwargs,
        )
        if msg.stop_reason == "refusal":
            return REFUSED
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return extract_json(text)

    async def judge(self, item: dict[str, Any]) -> Judgement:
        user = prompts.tag("guest_note", prompts.note_payload(item)) + "\n\nJudge this note."
        data = await self._ask(self.drafter, prompts.system(prompts.DRAFTER_SYSTEM), user)
        if data is REFUSED:
            return Judgement("routine", {}, None, "the drafter declined to read this note", None)
        return parse_judgement(data)

    async def redraft(self, item: dict[str, Any], draft: str | None, feedback: str) -> str | None:
        user = "\n".join([
            prompts.tag("guest_note", prompts.note_payload(item)),
            prompts.tag("previous_draft", draft or "(none)"),
            prompts.tag("what_to_change", feedback),
        ]) + "\n\nWrite the revised reply."
        data = await self._ask(self.drafter, prompts.system(prompts.REDRAFT_SYSTEM), user)
        return _str(data.get("draft")) if isinstance(data, dict) else None

    async def follow_up(self, item: dict[str, Any], ticket: str) -> str | None:
        reply = item.get("reply") or {}
        earlier = reply.get("body") if reply.get("status") == "published" else None
        user = "\n".join([
            prompts.tag("guest_note", prompts.note_payload(item)),
            prompts.tag("fixed_ticket", ticket),
            prompts.tag("earlier_reply", earlier or "(none)"),
        ]) + "\n\nWrite the follow-up reply."
        data = await self._ask(self.drafter, prompts.system(prompts.FOLLOW_UP_SYSTEM), user)
        return _str(data.get("draft")) if isinstance(data, dict) else None

    async def manage(self, item: dict[str, Any], j: Judgement, draft: str,
                     open_tickets: list[dict[str, Any]], fixed: str | None = None) -> Decision:
        extra = [f"The creators fixed this guest's ticket: {fixed}"] if fixed else []
        user = "\n".join([
            prompts.tag("open_tickets", json.dumps(open_tickets, ensure_ascii=False)),
            prompts.tag("guest_note", prompts.note_payload(item)),
            prompts.tag("drafter_judgement", json.dumps(j.as_audit(), ensure_ascii=False)),
            prompts.tag("draft_reply", draft),
        ]) + "\n\nReview the draft reply and answer with your decision."
        system = prompts.system(prompts.MANAGER_SYSTEM, extra)
        data = await self._ask(self.manager, system, user, think=True)
        if data is REFUSED:  # these come and go on ordinary notes, so one more try
            data = await self._ask(self.manager, system, user, think=True)
        if data is REFUSED:
            # A review that didn't happen is a hold, never a publish.
            return Decision("escalate", "the manager declined to review this note",
                            escalation="unsure", raw={"decision": "escalate", "refused": True})
        return parse_decision(data)

    async def review_skips(self, skipped: list[tuple[dict[str, Any], Judgement]]) -> set[int]:
        if not skipped:
            return set()
        listing = "\n".join(
            json.dumps({"note": json.loads(prompts.note_payload(i)), "drafter": j.as_audit()},
                       ensure_ascii=False)
            for i, j in skipped
        )
        data = await self._ask(self.manager, prompts.system(prompts.SKIPS_SYSTEM),
                               prompts.tag("skipped_notes", listing)
                               + "\n\nWhich of these deserve an answer after all?", think=True)
        ids = data.get("reconsider") if isinstance(data, dict) else None
        valid = {i["id"] for i, _ in skipped}
        return {x for x in (ids or []) if isinstance(x, int) and x in valid}
