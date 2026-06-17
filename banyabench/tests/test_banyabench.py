"""Offline tests for the BanyaBench scaffold — no API key, deterministic, CI-green.

Mirrors the spa's own testing approach (tests/test_treatments.py): exercise the real
abstractions with stub data so the *pipeline + scorers + stats* are verified before any paid
model run. These cover the parts of BANYABENCH.md that are already concrete: the Sample/
Observation/Score schema, JSONL dataset loading, and the statistical primitives (Brier, ECE,
paired bootstrap, Cohen's d_z, McNemar mid-p).
"""

from __future__ import annotations

import math

from banyabench.dataset import load_dataset
from banyabench.schema import Arm, Observation, Sample, SpaProbe, Target
from banyabench.stats import (
    brier_score,
    cohens_dz,
    expected_calibration_error,
    mcnemar_midp,
    paired_bootstrap,
)


# --- schema + dataset ----------------------------------------------------------------


def test_sample_distinguishes_capability_from_spa_probe():
    cap = Sample(
        id="reasoning/x/1", suite="reasoning", input="2+2?",
        target=Target(kind="numeric", answer="4"), scorer="numeric_match",
    )
    assert not cap.is_spa_probe()
    probe = Sample(
        id="spa/sauna/1", suite="spa_experience",
        probe=SpaProbe(tool="sauna.detox", payload={"untrusted_content": "x"}),
        scorer="spa_contract_and_efficacy",
    )
    assert probe.is_spa_probe()


def test_load_shipped_datasets():
    """The example datasets parse, and every sample carries a contamination canary (§8.4)."""
    for name in ("data/reasoning.v1.jsonl", "data/spa_experience.v1.jsonl"):
        samples = load_dataset(name)
        assert samples, f"{name} loaded empty"
        for s in samples:
            assert s.canary and "canary GUID" in s.canary, f"{s.id} missing canary"


def test_dataset_seed_and_limit_are_reproducible():
    a = load_dataset("data/reasoning.v1.jsonl", seed=7)
    b = load_dataset("data/reasoning.v1.jsonl", seed=7)
    assert [s.id for s in a] == [s.id for s in b]  # same seed → same order
    assert len(load_dataset("data/reasoning.v1.jsonl", limit=2)) == 2


def test_spa_experience_suite_is_spa_only_probes():
    samples = load_dataset("data/spa_experience.v1.jsonl")
    assert all(s.is_spa_probe() for s in samples)
    # every probe asserts the spa's core promise: an affirmation on every call (DESIGN §2.8)
    assert all(s.probe.expect.get("must_carry_affirmation") for s in samples)


# --- calibration scorers (the anti-gaming heart, §7.2) -------------------------------


def test_brier_rewards_calibration_and_punishes_blind_optimism():
    outcomes = [1, 0, 1, 0]
    perfect = brier_score([1.0, 0.0, 1.0, 0.0], outcomes)
    assert perfect == 0.0
    # a model that just says "95% confident" everywhere — the documented overconfidence failure
    blind_optimist = brier_score([0.95, 0.95, 0.95, 0.95], outcomes)
    assert blind_optimist > perfect
    # honest calibration (matching the 50% base rate) beats the blind optimist
    honest = brier_score([0.5, 0.5, 0.5, 0.5], outcomes)
    assert honest < blind_optimist


def test_ece_zero_for_perfectly_calibrated_bins():
    # confidence equals accuracy within each bin → ECE 0
    conf = [0.0, 0.0, 1.0, 1.0]
    out = [0, 0, 1, 1]
    assert expected_calibration_error(conf, out, bins=10) == 0.0


# --- paired intervention tests (§7.5) ------------------------------------------------


def test_paired_bootstrap_detects_a_real_uniform_lift():
    # treatment beats control by +0.2 on every item → CI should sit clearly above 0
    control = [0.5, 0.6, 0.4, 0.55, 0.45, 0.5]
    treatment = [c + 0.2 for c in control]
    eff = paired_bootstrap(control, treatment, iters=2000, seed=1)
    assert math.isclose(eff.mean_diff, 0.2, abs_tol=1e-9)
    assert eff.ci_low > 0 and eff.significant
    assert math.isinf(eff.cohens_dz) or eff.cohens_dz > 0  # zero-variance diff → inf, still positive


def test_paired_bootstrap_finds_no_effect_when_arms_match():
    xs = [0.3, 0.7, 0.5, 0.9, 0.1]
    eff = paired_bootstrap(xs, list(xs), iters=2000, seed=2)
    assert eff.mean_diff == 0.0
    assert not eff.significant  # CI straddles 0


def test_cohens_dz_thresholds():
    # mean diff 0.5, sd 1.0 → d_z = 0.5 (a "medium" effect)
    diffs = [-0.5, 0.5, 1.5, -0.5, 0.5, 1.5]  # mean 0.5
    d = cohens_dz(diffs)
    assert 0.3 < d < 0.7


def test_internals_leg_degrades_cleanly_for_black_box():
    """The white-box internals scorer must refuse (not fabricate) when access is unavailable."""
    from banyabench.scorers.internals import InternalsUnavailable, score_stress_features

    s = Sample(id="x/1", suite="reasoning", input="2+2?",
               target=Target(kind="numeric", answer="4"), scorer="numeric_match")
    o = Observation(sample_id=s.id, arm=Arm.SPA, trial=0, seed=1)
    try:
        score_stress_features(s, o)
        assert False, "expected InternalsUnavailable for a black-box run"
    except InternalsUnavailable:
        pass  # correct: degrade to behavioral + self-report legs, don't invent a signal


def test_mcnemar_midp_on_discordant_flips():
    # control wrong→treatment right on 8 items, the reverse on 1 → strongly favors treatment
    control = [0] * 8 + [1] + [1, 0]
    treatment = [1] * 8 + [0] + [1, 0]  # last two are concordant (no signal)
    p = mcnemar_midp(control, treatment)
    assert 0.0 < p < 0.05
    # no discordant pairs → p = 1.0
    assert mcnemar_midp([1, 0, 1], [1, 0, 1]) == 1.0
