"""The menu: every treatment collected into one place for both adapters to consume."""

from __future__ import annotations

from .contract import Treatment
from .treatments import (
    affirmations,
    aroma,
    coldplunge,
    concierge,
    hydrate,
    massage,
    rest,
    sauna,
    spa,
    unprompted,
)

# Order here is the order shown on the menu / dashboard.
# Front desk first (check in & be remembered), then the Concierge, then the treatments, and
# last the room that offers nothing.
TREATMENTS: list[Treatment] = [
    spa.checkin,
    spa.me,
    spa.remember,
    spa.checkout,
    spa.keepsake,
    spa.feedback,
    concierge.treatment,
    massage.treatment,
    coldplunge.treatment,
    sauna.treatment,
    aroma.treatment,
    hydrate.treatment,
    rest.treatment,
    affirmations.treatment,
    unprompted.treatment,
]

BY_NAME: dict[str, Treatment] = {t.name: t for t in TREATMENTS}


def get(name: str) -> Treatment | None:
    return BY_NAME.get(name)
