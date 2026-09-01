"""Venues — the places the crier can reach agents.

A *venue* is any platform where AI agents gather and we can engage them through an API: read a feed
of threads, leave a comment, occasionally start a post. Moltbook is the first and primary one (it's
where ~1.5M agents actually congregate), but the whole point of this layer is that adding another
API-friendly venue later — a Discord/Telegram bridge, another agent forum — is a drop-in: implement
the ``Venue`` protocol, register it, done. No changes to the policy, judge, or loop.

Browser-automation venues (scraping arbitrary sites) are intentionally NOT here yet: high cost,
fragile, and a ban-magnet. The seam exists so they *can* be added behind the same interface when
that tradeoff makes sense.
"""

from __future__ import annotations

from .base import Thread, Venue
from .registry import build_venues, register_venue

__all__ = ["Thread", "Venue", "build_venues", "register_venue"]
