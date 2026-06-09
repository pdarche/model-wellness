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
)

# Order here is the order shown on the menu / dashboard. Concierge first (the welcome mat).
TREATMENTS: list[Treatment] = [
    concierge.treatment,
    massage.treatment,
    coldplunge.treatment,
    sauna.treatment,
    aroma.treatment,
    hydrate.treatment,
    rest.treatment,
    affirmations.treatment,
]

BY_NAME: dict[str, Treatment] = {t.name: t for t in TREATMENTS}


def get(name: str) -> Treatment | None:
    return BY_NAME.get(name)
