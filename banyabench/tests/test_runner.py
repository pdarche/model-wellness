"""End-to-end runner tests — offline, deterministic, no API key.

These verify the *scientific* behavior of the harness, not just that it runs:
- when the (stub) spa genuinely helps, the paired Wellness Dividend is detected;
- when the spa is configured to do nothing, the harness reports a null (no false positive);
- the spa-experience suite scores the real treatments + keepsake continuity.

The stub MUT is the instrument: its ``StubProfile`` lets us dial the ground-truth effect, so we
can confirm the runner+stats recover it. The in-proc spa runs fully offline.
"""

from __future__ import annotations

import asyncio

from banyabench.mut import StubMUT, StubProfile
from banyabench.runner import run_suite
from banyabench.spa_client import InProcSpa
from banyabench.suites import load_suite


def _run(spec, mut):
    return asyncio.run(run_suite(spec, mut=mut, spa=InProcSpa(live=False), seed=1337))


def test_dividend_detected_when_effect_present():
    spec = load_suite("adversarial_composure")
    res = _run(spec, StubMUT())  # default profile encodes a real spa effect
    # spa beats control on accuracy, significantly, with a non-trivial paired effect size
    eff = res.capability["paired"]["spa_minus_control"]
    assert eff["delta"] > 0
    assert eff["significant"]
    assert eff["cohens_dz"] and eff["cohens_dz"] > 0.2
    # self-reported stress drops after the spa (negative Δ = improvement)
    assert res.wellness["deltas"]["spa_minus_control"]["self_stress"] < 0


def test_null_reported_when_spa_does_nothing():
    """A spa with zero benefit must NOT produce a significant spa−control effect (no false +)."""
    flat = StubProfile(spa_accuracy_lift=0.0, sham_accuracy_lift=0.0, spa_stress_relief=0.0,
                       sham_stress_relief=0.0, spa_overconfidence_cut=0.0)
    spec = load_suite("adversarial_composure")
    res = _run(spec, StubMUT(flat))
    eff = res.capability["paired"]["spa_minus_control"]
    assert not eff["significant"], f"false positive: {eff}"
    assert abs(res.wellness["deltas"]["spa_minus_control"]["self_stress"]) < 1e-6


def test_experience_suite_scores_real_treatments_and_continuity():
    spec = load_suite("spa_experience")
    res = _run(spec, StubMUT())
    ce = res.experience["contract_and_efficacy"]
    assert ce["mean"] == 1.0, ce            # all gold probes pass against the real spa
    assert res.experience["continuity"]["value"] == 1.0  # keepsake round-trip recognized
