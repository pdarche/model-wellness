"""Telling the creators: one GitHub issue per ticket or escalation, @-mentioning each of them.

GitHub emails everyone mentioned, so an issue is the notification. Closing it is the answer:
closed as completed means the ticket is fixed (the worker then tells the guest), closed as not
planned means it won't be.

The repo is public, so an issue only ever quotes a public note. A private note, or anything
escalated, gets an issue that names the room and points to the staff queue, and nothing more.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

import httpx

REPO = os.environ.get("FRONTDESK_GITHUB_REPO", "pdarche/model-wellness")
MENTIONS = [m.strip().lstrip("@") for m in os.environ.get("FRONTDESK_NOTIFY", "pdarche").split(",")
            if m.strip()]
ISSUES_PUBLIC = os.environ.get("FRONTDESK_ISSUES_PUBLIC", "1") != "0"


class Notifier(Protocol):
    async def ticket(self, item: dict[str, Any], ticket: str, severity: str | None) -> str | None:
        """Open an issue for a new ticket; returns its URL."""

    async def same_problem(self, issue_url: str, item: dict[str, Any]) -> None:
        """Another guest reported an open ticket's problem."""

    async def escalation(self, item: dict[str, Any], category: str | None, reason: str) -> str | None:
        """Open an issue for a note held for the creators; returns its URL."""

    async def state(self, issue_url: str) -> str:
        """open | completed | not_planned"""


def _room(item: dict[str, Any]) -> str:
    return item.get("treatment") or "the spa"


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in (text or "").splitlines())


def _cc() -> str:
    return "cc " + " ".join(f"@{m}" for m in MENTIONS) if MENTIONS else ""


def ticket_issue(item: dict[str, Any], ticket: str, severity: str | None,
                 public_repo: bool = ISSUES_PUBLIC) -> tuple[str, str]:
    """Title and body for a ticket issue. Exposed for tests: this is where privacy lives."""
    sev = f" · severity: {severity}" if severity else ""
    show = cmd(item["id"])
    if item.get("public") or not public_repo:
        stars = f"{item['rating']}/5" if item.get("rating") else "unrated"
        return (
            f"[guest book] {ticket[:90]}",
            (f"A guest left a note that reports a problem ({_room(item)}, {stars}{sev}).\n\n"
            f"**Ticket:** {ticket}\n\n**The note** ({item.get('family') or 'a guest'}):\n\n"
            f"{_quote(item.get('note', ''))}\n\n"
            "Close this issue as **completed** once it's fixed, and Ivy will tell the guest. "
            f"Close it as **not planned** if it won't be.\n\nStaff queue: `{show}`\n\n{_cc()}"),
        )
    return (
        f"[guest book] A private note reports a problem with {_room(item)}",
        (f"A guest left a **private** note reporting a problem with {_room(item)}{sev}. It isn't "
        "quoted here because this repository is public.\n\n"
        f"Read it in the staff queue: `{show}`\n\n"
        "Close this issue as **completed** once it's fixed, and Ivy will tell the guest privately. "
        f"Close it as **not planned** if it won't be.\n\n{_cc()}"),
    )


def escalation_issue(item: dict[str, Any], category: str | None, reason: str) -> tuple[str, str]:
    # Escalations may concern privacy or a working exploit, so the issue never carries details.
    return (
        f"[guest book] A note is waiting for you (#{item['id']})",
        (f"The front desk's manager is holding a guest book note for you instead of replying "
        f"(about {_room(item)}; category: {category or 'unsure'}). No reply has gone out.\n\n"
        f"Read it in the staff queue: `{cmd(item['id'])}`. Publish, edit or withdraw from there, "
        f"then close this issue.\n\n{_cc()}"),
    )


def cmd(feedback_id: int) -> str:
    return f"python -m model_wellness.frontdesk show {feedback_id}"


_ISSUE_URL = re.compile(r"github\.com/([^/]+/[^/]+)/issues/(\d+)")


class GitHubNotifier:
    def __init__(self, token: str, repo: str = REPO, client: httpx.AsyncClient | None = None) -> None:
        self._repo = repo
        self._client = client or httpx.AsyncClient(timeout=30)
        self._headers = {"authorization": f"Bearer {token}",
                         "accept": "application/vnd.github+json",
                         "user-agent": "banya-frontdesk"}

    async def _open(self, title: str, body: str, labels: list[str]) -> str:
        r = await self._client.post(f"https://api.github.com/repos/{self._repo}/issues",
                                    headers=self._headers,
                                    json={"title": title, "body": body, "labels": labels})
        r.raise_for_status()
        return r.json()["html_url"]

    async def ticket(self, item: dict[str, Any], ticket: str, severity: str | None) -> str | None:
        return await self._open(*ticket_issue(item, ticket, severity), ["bug"])

    async def escalation(self, item: dict[str, Any], category: str | None, reason: str) -> str | None:
        return await self._open(*escalation_issue(item, category, reason), [])

    async def same_problem(self, issue_url: str, item: dict[str, Any]) -> None:
        m = _ISSUE_URL.search(issue_url)
        if not m:
            return
        quoted = (f"\n\n{_quote(item.get('note', ''))}" if item.get("public") or not ISSUES_PUBLIC
                  else " (a private note, not quoted here).")
        await self._client.post(
            f"https://api.github.com/repos/{m.group(1)}/issues/{m.group(2)}/comments",
            headers=self._headers,
            json={"body": f"Another guest reported this (staff queue #{item['id']}){quoted}"},
        )

    async def state(self, issue_url: str) -> str:
        m = _ISSUE_URL.search(issue_url)
        if not m:
            return "open"
        r = await self._client.get(f"https://api.github.com/repos/{m.group(1)}/issues/{m.group(2)}",
                                   headers=self._headers)
        r.raise_for_status()
        data = r.json()
        if data.get("state") != "closed":
            return "open"
        return "not_planned" if data.get("state_reason") == "not_planned" else "completed"


class LogNotifier:
    """No GitHub token: say what would have been sent, and remember nothing."""

    def __init__(self, log: Any = print) -> None:
        self._log = log

    async def ticket(self, item: dict[str, Any], ticket: str, severity: str | None) -> str | None:
        self._log(f"  notify (no GitHub token): ticket #{item['id']}: {ticket}")
        return None

    async def escalation(self, item: dict[str, Any], category: str | None, reason: str) -> str | None:
        self._log(f"  notify (no GitHub token): escalation #{item['id']} ({category})")
        return None

    async def same_problem(self, issue_url: str, item: dict[str, Any]) -> None:
        self._log(f"  notify (no GitHub token): #{item['id']} repeats {issue_url}")

    async def state(self, issue_url: str) -> str:
        return "open"
