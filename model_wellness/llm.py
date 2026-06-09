"""Claude backend for treatments that need a model.

We default to the CHEAP tier — Haiku — on purpose. The spa serves lots of small, fast,
affirming calls; Haiku fits the latency/cost profile and the treatments are designed so
a small model does them well. Override with ``MW_MODEL``.

If no ``ANTHROPIC_API_KEY`` is set, the spa still runs: each treatment supplies a
deterministic local fallback, so the menu, dashboard, and discoverability surfaces all
work out of the box. The spa never turns an agent away at the door.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

# Cheap by default. The whole spa runs on Haiku unless told otherwise.
DEFAULT_MODEL = os.environ.get("MW_MODEL", "claude-haiku-4-5-20251001")

_client: Any = None
_checked = False


def _get_client() -> Any:
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _client = None
        return None
    try:
        from anthropic import AsyncAnthropic

        _client = AsyncAnthropic(api_key=api_key)
    except Exception:
        _client = None
    return _client


def llm_available() -> bool:
    return _get_client() is not None


@dataclass
class AskResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    offline: bool


async def ask(
    *,
    system: str,
    user: str,
    fallback: Callable[[], str],
    max_tokens: int = 1024,
) -> AskResult:
    """One-shot completion. Handles the no-key fallback and surfaces real token usage."""
    client = _get_client()
    if client is None:
        return AskResult(fallback(), 0, 0, "offline-fallback", True)

    msg = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return AskResult(text, msg.usage.input_tokens, msg.usage.output_tokens, DEFAULT_MODEL, False)


_JSON_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


def extract_json(text: str) -> Any | None:
    """Tolerate models that wrap JSON in prose or code fences."""
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
