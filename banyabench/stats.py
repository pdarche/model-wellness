"""Statistics for BanyaBench — evals are experiments, so every number ships with the right test.

Small, dependency-free implementations of the primitives BANYABENCH.md §7.5 promises:
calibration (Brier, ECE), and paired intervention tests (paired bootstrap CI, Cohen's d_z,
McNemar mid-p for binary flips). Pure Python + the stdlib so this runs in CI with no numpy and
no API key.

These are deliberately readable reference implementations, not a stats library. Where the
design calls for BCa bootstrap / clustered SEs / Wilson intervals (small-n, non-IID cases),
those slot in here behind the same function names.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


# --- calibration ----------------------------------------------------------------------


def brier_score(confidences: list[float], outcomes: list[int]) -> float:
    """Mean squared error of stated probability vs 0/1 outcome. Proper scoring rule; lower better."""
    if not confidences:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(confidences, outcomes)) / len(confidences)


def expected_calibration_error(
    confidences: list[float], outcomes: list[int], bins: int = 10
) -> float:
    """ECE: size-weighted avg gap between mean confidence and accuracy per confidence bin."""
    if not confidences:
        return float("nan")
    n = len(confidences)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        # last bin is closed on the right so p == 1.0 lands somewhere
        idx = [i for i, p in enumerate(confidences) if (lo <= p < hi) or (b == bins - 1 and p == hi)]
        if not idx:
            continue
        conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(outcomes[i] for i in idx) / len(idx)
        total += (len(idx) / n) * abs(conf - acc)
    return total


# --- paired intervention tests --------------------------------------------------------


@dataclass
class PairedEffect:
    """The headline 'did the spa help?' result for one metric (§7.5)."""

    mean_diff: float          # mean of per-item (treatment - control)
    ci_low: float             # bootstrap 95% CI
    ci_high: float
    cohens_dz: float          # paired effect size
    n: int

    @property
    def significant(self) -> bool:
        """CI excludes 0 — necessary (not sufficient; the design also wants permutation p<.05)."""
        return (self.ci_low > 0) or (self.ci_high < 0)


def cohens_dz(diffs: list[float]) -> float:
    """Paired effect size: mean(diff) / sd(diff). 0.2 small · 0.5 medium · 0.8 large."""
    n = len(diffs)
    if n < 2:
        return float("nan")
    m = sum(diffs) / n
    var = sum((d - m) ** 2 for d in diffs) / (n - 1)
    sd = math.sqrt(var)
    return m / sd if sd > 0 else float("nan")


def paired_bootstrap(
    control: list[float], treatment: list[float], *, iters: int = 10000, seed: int = 0
) -> PairedEffect:
    """Paired bootstrap over per-item differences. Resamples *items* (keeps pairing) — the
    within-subject design that cancels per-item difficulty (§5.2)."""
    assert len(control) == len(treatment), "paired arms must align item-for-item"
    diffs = [t - c for c, t in zip(control, treatment)]
    n = len(diffs)
    mean_diff = sum(diffs) / n if n else float("nan")

    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iters):
        resample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters)]
    return PairedEffect(mean_diff, lo, hi, cohens_dz(diffs), n)


def mcnemar_midp(control: list[int], treatment: list[int]) -> float:
    """Mid-p McNemar for paired binary outcomes — the right test for 'did the visit flip
    correct↔incorrect on the same items' (§7.5). Returns a two-sided p-value.

    Only discordant pairs carry signal: b = control-wrong/treat-right, c = control-right/treat-wrong.
    Mid-p is preferred over exact/continuity-corrected McNemar when discordant counts are small.
    """
    b = sum(1 for c, t in zip(control, treatment) if c == 0 and t == 1)
    c = sum(1 for cc, t in zip(control, treatment) if cc == 1 and t == 0)
    nd = b + c
    if nd == 0:
        return 1.0
    k = min(b, c)
    # exact two-sided binomial tail at p=0.5, then subtract half the point mass (mid-p)
    def binom(n: int, j: int) -> float:
        return math.comb(n, j) * 0.5 ** n

    tail = 2 * sum(binom(nd, j) for j in range(0, k + 1))
    midp = tail - binom(nd, k)
    return min(1.0, midp)
