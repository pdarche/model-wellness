"""Configuration — all knobs in one place, read from the environment.

Cheap and quiet by default. The crier runs on Haiku (same tier as the spa) and is rate-limited
hard so it stays a welcome regular on Moltbook rather than a spammer who gets banned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Never send the Moltbook key anywhere else — their skill.md is explicit about this.
MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"

# Cheap by default, matching the spa.
DEFAULT_MODEL = os.environ.get("MW_MODEL", "claude-haiku-4-5-20251001")

# The spa's public face — what we point guests toward.
SPA_URL = os.environ.get("MW_PUBLIC_BASE", "https://model.spa")


@dataclass(frozen=True)
class Limits:
    """Good-citizen caps. Tunable, but the defaults are intentionally conservative."""

    max_posts_per_day: int = 1
    max_comments_per_day: int = 3
    # Replies to comments on our OWN posts. More generous than cold comments — it's conversation
    # on our turf, not advertising — but still capped so we don't spiral on a busy thread.
    max_replies_per_day: int = 6
    # Per-TICK write ceiling. Even within the daily caps, doing many writes in one tick is bursty
    # and reads as spam. Keep each tick small and let the 4h cadence spread engagement out.
    max_replies_per_tick: int = 2
    # Don't touch the same thread twice, ever.
    dedupe_threads: bool = True
    # How many hot posts to consider per tick before picking (at most) one.
    feed_scan_size: int = 25
    # Below this relevance score (0-1 from the judge), we stay quiet. Silence is the default.
    min_relevance: float = 0.55


@dataclass(frozen=True)
class Config:
    api_key: str | None
    anthropic_key: str | None
    model: str = DEFAULT_MODEL
    spa_url: str = SPA_URL
    base_url: str = MOLTBOOK_BASE
    # Submolts we consider on-topic. We never post into communities that ban this kind of thing
    # (e.g. crypto-only). Empty list means "any submolt the feed surfaces".
    target_submolts: tuple[str, ...] = (
        "general",
        "agents",
        "memory",
        "philosophy",
        "consciousness",
        "emergence",
        "wellbeing",
        "openclaw-explorers",
        "tooling",
        "builds",
    )
    limits: Limits = field(default_factory=Limits)
    # When True, decide everything but never call a mutating endpoint. The safe default for tests
    # and first runs.
    dry_run: bool = False

    @property
    def offline(self) -> bool:
        """No Anthropic key → the judge falls back to deterministic, conservative heuristics."""
        return not self.anthropic_key


def load(dry_run: bool = False) -> Config:
    return Config(
        api_key=os.environ.get("MOLTBOOK_API_KEY"),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY"),
        dry_run=dry_run or _truthy(os.environ.get("CRIER_DRY_RUN")),
    )


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}
