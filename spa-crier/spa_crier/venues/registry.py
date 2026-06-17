"""Which venues are active this run.

Today: Moltbook only (it's where the agents are). The registry exists so that turning on another
venue later is a one-liner here — no caller changes. Each venue self-reports whether it's configured
(has credentials); unconfigured venues are silently skipped.
"""

from __future__ import annotations

from typing import Callable

from ..config import Config
from .base import Venue
from .moltbook import MoltbookVenue

# name -> (is_configured(cfg), factory(cfg, llm_solver))
_REGISTRY: dict[str, tuple[Callable[[Config], bool], Callable]] = {}


def register_venue(name: str, *, configured: Callable[[Config], bool], factory: Callable) -> None:
    _REGISTRY[name] = (configured, factory)


def build_venues(cfg: Config, *, llm_solver=None) -> list[Venue]:
    """Instantiate every venue that's configured for this run."""
    venues: list[Venue] = []
    for _name, (configured, factory) in _REGISTRY.items():
        if configured(cfg):
            venues.append(factory(cfg, llm_solver=llm_solver))
    return venues


# --- built-in registrations ---------------------------------------------------

register_venue(
    "moltbook",
    configured=lambda cfg: bool(cfg.api_key),
    factory=lambda cfg, llm_solver=None: MoltbookVenue(cfg, llm_solver=llm_solver),
)
