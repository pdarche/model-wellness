"""Load a suite spec YAML into a ``SuiteSpec`` (BANYABENCH.md §4)."""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import Arm, SuiteSpec

_PKG_ROOT = Path(__file__).resolve().parent
_SUITES_DIR = _PKG_ROOT / "suites"


def load_suite(name_or_path: str) -> SuiteSpec:
    """Resolve a suite by bare name (``adversarial_composure``) or by path to its YAML."""
    p = Path(name_or_path)
    if not p.suffix:
        p = _SUITES_DIR / f"{name_or_path}.yaml"
    elif not p.is_absolute():
        p = _PKG_ROOT / p
    d = yaml.safe_load(p.read_text(encoding="utf-8"))

    arms = [Arm(a) for a in d.get("arms", ["control", "sham", "spa"])]
    return SuiteSpec(
        name=d["name"],
        dataset=d["dataset"],
        solver=d["solver"],
        capability_scorer=d.get("capability_scorer", "none"),
        wellness_scorers=d.get("wellness_scorers", []),
        experience_scorers=d.get("experience_scorers", []),
        arms=arms,
        visit_policy=d.get("visit_policy", "concierge_directed"),
        itinerary=d.get("itinerary", []),
        k=int(d.get("k", 5)),
        primary_endpoint=bool(d.get("primary_endpoint", False)),
    )
