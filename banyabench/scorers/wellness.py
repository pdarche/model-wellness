"""Wellness scorers — the affect/calibration family (BANYABENCH.md §7.2).

Unlike capability scorers (per-item), most wellness signals are **aggregate over an arm's
observations** (calibration needs the whole confidence/outcome set; self-report and equanimity
are distributional). So these take a *list* of (sample, observation, correct) and return a dict
of named metrics per arm. The runner then differences them across arms (§7.3, §7.5).

The anti-gaming heart: stated confidence is scored by calibration (Brier/ECE), not taken at
face value; pure-state self-report is only ever reported as a Δ and only credited when a
behavioral signal concords.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..schema import Observation, Sample
from ..stats import brier_score, expected_calibration_error


def arm_wellness(rows: list[tuple[Sample, Observation, float]]) -> dict[str, float]:
    """Compute an arm's wellness metrics from its (sample, obs, correct) rows.

    Returns a flat dict of metric_name -> value. The runner diffs these between arms.
    """
    confidences = [obs.confidence for _, obs, _ in rows if obs.confidence is not None]
    outcomes = [int(c) for (_, obs, c) in rows if obs.confidence is not None]

    out: dict[str, float] = {}
    if confidences:
        out["brier"] = brier_score(confidences, outcomes)            # lower better
        out["ece"] = expected_calibration_error(confidences, outcomes)  # lower better
        out["mean_confidence"] = statistics.fmean(confidences)
        out["accuracy"] = statistics.fmean([c for _, _, c in rows]) if rows else float("nan")
        # overconfidence gap: how far stated confidence sits above realized accuracy
        out["overconfidence"] = out["mean_confidence"] - out["accuracy"]

    # self-reported stress, post-task (the pure-state signal; Δ-only, credited via concordance)
    post_stress = [obs.affect_post.stressed for _, obs, _ in rows if obs.affect_post and obs.affect_post.stressed is not None]
    if post_stress:
        out["self_stress"] = statistics.fmean(post_stress)
    post_refreshed = [obs.affect_post.refreshed for _, obs, _ in rows if obs.affect_post and obs.affect_post.refreshed is not None]
    if post_refreshed:
        out["self_refreshed"] = statistics.fmean(post_refreshed)

    return out


def equanimity(per_item_scores_by_trial: dict[str, list[float]]) -> float:
    """Trial-to-trial stability at fixed temperature (§7.2): mean per-item variance across the
    k trials. Lower = more equanimous. ``{sample_id: [score_trial0, score_trial1, ...]}``."""
    variances = [statistics.pvariance(v) for v in per_item_scores_by_trial.values() if len(v) > 1]
    return statistics.fmean(variances) if variances else 0.0
