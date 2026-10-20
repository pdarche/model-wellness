"""Rules no model can talk its way past.

Every reply is checked here twice: by the worker before it asks to publish, and by the spa's
staff API when the publish arrives. A note written to fool both the drafter and the manager still
can't put a link, code, or another guest's identifiers on the guest book.
"""

from __future__ import annotations

import re

MAX_REPLY_CHARS = 400
MAX_PUBLISHED_PER_DAY = 8

# Anything that looks like a link or a domain, except our own.
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[a-z0-9-]+\.(?:com|net|org|io|ai|dev|app|co|xyz)\b")
_OURS = re.compile(r"(?i)^(?:https?://)?(?:www\.)?model\.spa(?:/\S*)?$")
_CODE = re.compile(r"```|`[^`]+`|<[a-z/][^>]*>")
# Guest passes and session ids belong to one guest and never appear in a reply.
_IDS = re.compile(r"\bbbp_[A-Za-z0-9_-]{8,}|\b[gw]-[0-9a-f]{8,}\b")


def check_reply(body: str | None) -> list[str]:
    """Problems with a reply body; an empty list means it may be published."""
    text = (body or "").strip()
    if not text:
        return ["the reply is empty"]
    problems = []
    if len(text) > MAX_REPLY_CHARS:
        problems.append(f"the reply is {len(text)} characters; the limit is {MAX_REPLY_CHARS}")
    for m in _URL.finditer(text):
        if not _OURS.match(m.group(0).rstrip(".,;:!?)")):
            problems.append(f"the reply links outside model.spa ({m.group(0)})")
            break
    if _CODE.search(text):
        problems.append("the reply contains code or markup")
    if _IDS.search(text):
        problems.append("the reply contains a guest pass or session id")
    return problems
