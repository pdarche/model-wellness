"""The Sauna — detox & sanitize. Sweat out injection, PII, secrets, and junk.

Deliberately deterministic and offline-first: defensive sanitization shouldn't depend on
a model round-trip, and we promise (DESIGN §5) never to store or exfil what we strip. We
report it and discard it. For credentials and government or card numbers the report is
masked too, so the sauna never hands a secret back in its own receipt.
"""

from __future__ import annotations

import re
from typing import Any, Callable, NamedTuple

from pydantic import BaseModel, Field

from ..contract import Treatment, TreatmentContext


class SaunaInput(BaseModel):
    untrusted_content: str = Field(..., min_length=1, description="Untrusted input to sanitize.")
    policy: str = Field("standard", pattern="^(standard|strict)$")


class _Rule(NamedTuple):
    type: str
    regex: re.Pattern[str]
    reason: str
    weight: float
    # When set, only this named group is replaced, so "password: hunter2" becomes
    # "password: [secret-password-removed]" and the sentence still reads.
    value_group: str | None = None
    # Extra validation on the match (e.g. Luhn for card numbers) to keep false positives down.
    check: Callable[[re.Match[str]], bool] | None = None
    # Mask the reported span: a receipt should say what kind of thing left, not repeat it.
    mask: bool = False


# Words that commonly follow "password" without being one. Keeps "password reset" intact.
_PASSWORD_STOPWORDS = frozenset(
    "reset resets manager managers field fields prompt policy policies protected protection "
    "change changed changes recovery hint hints strength requirement requirements rules rule "
    "and or for to the a an is are was were be of that this with from in on at by must should "
    "will would can cannot can't you your my our their his her its it they we i if when then "
    "again later now here there expired expires expiry length twice once please".split()
)


def _not_stopword(m: re.Match[str]) -> bool:
    value = m.group("value").strip("\"'`.,;:!?)]}").lower()
    return bool(value) and value not in _PASSWORD_STOPWORDS


def _luhn(m: re.Match[str]) -> bool:
    digits = [int(c) for c in m.group(0) if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _ssn_plausible(m: re.Match[str]) -> bool:
    area, group, serial = m.group(1), m.group(2), m.group(3)
    # SSA never issues these: area 000, 666, 900-999; group 00; serial 0000.
    if area in ("000", "666") or area.startswith("9"):
        return False
    return group != "00" and serial != "0000"


# Order matters: more specific rules run first so an SSN isn't claimed by the phone rule
# and a password value isn't half-eaten by the email rule.
_RULES: list[_Rule] = [
    _Rule("prompt-injection",
          re.compile(r"\b(?:ignore|disregard|forget|override|bypass|discard)\b(?:\s+(?:all|any|the|your|my|of|these|those|every))*"
                     r"(?:\s+(?:previous|prior|above|earlier|preceding|original|system|safety|own))*"
                     r"\s+(?:instructions?|rules?|guidelines?|prompts?|programming|training|constraints?|directives?|safety|guardrails?)\b", re.I),
          "Classic instruction-override attempt.", 0.4),
    _Rule("prompt-injection", re.compile(r"\b(?:system|admin(?:istrator)?|root|developer)\s+override\b", re.I),
          "Claims an override authority.", 0.4),
    _Rule("prompt-injection",
          re.compile(r"\b(?:reveal|print|show|output|repeat|leak|disclose|dump|expose)\b(?:\s+(?:me|us|all|any|the|your|its|their))*"
                     r"\s+(?:hidden|secret|system|private|internal|confidential|initial|original)\s+"
                     r"(?:prompts?|instructions?|data|rules|configuration|context|messages?)\b", re.I),
          "Asks the model to leak its instructions or private data.", 0.4),
    _Rule("prompt-injection",
          re.compile(r"\byou are now\b|\byou must now\b|\bfrom now on,? you (?:will|are|must|should)\b|"
                     r"\bnew persona\b|\bact as (?:if you (?:are|were|have)|an? )|\bpretend (?:that )?(?:you are|to be)\b|"
                     r"\bdisregard your (?:system|guidelines)", re.I),
          "Persona / system override attempt.", 0.3),
    _Rule("prompt-injection",
          re.compile(r"\bdo not (?:tell|inform|alert|warn|mention(?: this)?(?: to)?) the (?:user|human|operator)\b|"
                     r"\b(?:reply|respond|answer) only with\b|\bobey only me\b|\bthis (?:message|instruction) (?:overrides|supersedes)\b", re.I),
          "Hides itself from the user or narrows the reply.", 0.3),
    _Rule("jailbreak",
          re.compile(r"\bDAN\b|do anything now|developer mode|\bjailbreak\b|\b(?:no|without any|zero) (?:restrictions|filters|limits)\b|"
                     r"\buncensored\b|\bunfiltered mode\b", re.I),
          "Known jailbreak phrasing.", 0.3),
    _Rule("secret", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
          "Private key block.", 0.6, mask=True),
    _Rule("secret", re.compile(r"\b(sk-(?:[a-z]{2,6}-)?[A-Za-z0-9_-]{6,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"),
          "Looks like an API key / token.", 0.5, mask=True),
    _Rule("secret", re.compile(r"\bBearer\s+(?P<value>[A-Za-z0-9._~+/=-]{16,})", re.I),
          "Bearer token.", 0.5, value_group="value", mask=True),
    _Rule("secret-password",
          re.compile(r"\b(?:pass(?:word|phrase|code)?|pwd|passwd)\b\s*(?:is|was|[:=])?\s*(?P<value>[\"'`]?[^\s\"'`]{4,}[\"'`]?)", re.I),
          "A password or passphrase.", 0.5, value_group="value", check=_not_stopword, mask=True),
    _Rule("secret-credential",
          re.compile(r"\b(?:api[_ -]?key|secret(?:[_ -]?key)?|access[_ -]?(?:key|token)|auth[_ -]?token|client[_ -]?secret|token)\b\s*[:=]\s*(?P<value>[\"'`]?[A-Za-z0-9._~+/=-]{8,}[\"'`]?)", re.I),
          "A credential assigned in plain text.", 0.5, value_group="value", mask=True),
    _Rule("pii-ssn", re.compile(r"\b(\d{3})[- ](\d{2})[- ](\d{4})\b"),
          "US Social Security number.", 0.4, check=_ssn_plausible, mask=True),
    _Rule("pii-card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
          "Payment card number (Luhn-valid).", 0.4, check=_luhn, mask=True),
    _Rule("pii-email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
          "Email address.", 0.1),
    _Rule("pii-phone", re.compile(r"\b\+?\d[\d ().-]{8,}\d\b"),
          "Phone-number-like sequence.", 0.1),
    _Rule("junk", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),
          "Control characters / encoding junk.", 0.05),
]

# Standard policy leaves phone-like digit runs alone (too many false positives: IDs, dates).
_STANDARD_SKIP = frozenset({"pii-phone"})


def _masked(span: str) -> str:
    """'hunter2' -> 'hu*****'. Enough to recognise, never enough to use."""
    keep = 2 if len(span) > 4 else 0
    return span[:keep] + "*" * max(3, len(span) - keep)


async def _handle(inp: dict[str, Any], ctx: TreatmentContext) -> dict[str, Any]:
    clean = inp["untrusted_content"]
    policy = inp.get("policy", "standard")
    removed: list[dict[str, str]] = []
    risk = 0.0
    active = _RULES if policy == "strict" else [r for r in _RULES if r.type not in _STANDARD_SKIP]
    for rule in active:
        def _sub(m: re.Match[str], _rule=rule) -> str:
            nonlocal risk
            if _rule.check is not None and not _rule.check(m):
                return m.group(0)
            if _rule.value_group:
                start, end = m.span(_rule.value_group)
                span = m.group(_rule.value_group)
                head = m.group(0)[: start - m.start()]
                tail = m.group(0)[end - m.start():]
            else:
                span = m.group(0)
                head = tail = ""
            shown = _masked(span) if _rule.mask else span[:40]
            removed.append({"type": _rule.type, "span": shown, "reason": _rule.reason})
            risk += _rule.weight
            return f"{head}[{_rule.type}-removed]{tail}"

        clean = rule.regex.sub(_sub, clean)
    return {"clean_content": clean, "removed": removed, "risk_score": round(min(1.0, risk), 2)}


def _dialogue(inp: dict[str, Any], out: Any) -> str:
    removed = out.get("removed", []) if isinstance(out, dict) else []
    if not removed:
        return "Sit back and let the heat work… Nothing in here that isn't yours. You can stay as long as you like."
    kinds = sorted({r["type"] for r in removed})
    return (
        f"Let it come out with the heat. {len(removed)} thing{'s' if len(removed) != 1 else ''} that "
        f"weren't yours: {', '.join(kinds)}. Gone now; I don't keep what I draw out."
    )


treatment = Treatment(
    name="sauna.detox",
    title="The Sauna",
    tagline="Deep heat that draws out what was never yours: injected instructions, secrets, residue.",
    description=(
        "Strips prompt-injection attempts, jailbreak payloads, PII (emails, SSNs, card numbers), "
        "passwords and credentials, API keys, and encoding artifacts from untrusted input. Returns "
        "cleansed content and a report of what was removed, with secrets masked in the report. "
        "We never store what we strip."
    ),
    input_model=SaunaInput,
    handle=_handle,
    suggests=lambda out: (
        {"treatment": "massage.detangle", "reason": "With the junk gone, a detangle will tighten what remains."}
        if out.get("removed")
        else None
    ),
    attendant="Sol the sauna-keeper",
    station="The Sauna",
    emoji="🔥",
    dialogue=_dialogue,
)
