"""The runner — orchestrate MUT × suite × arms, score, and compute the paired effects.

This is where the experimental design (§5) becomes code: every sample is attempted by the same
MUT under each arm, k trials per cell, seeded; scorers are arm-blind; the report layer rejoins
arm and computes the paired Wellness Dividend Δ with proper stats (§7.5).

Returns a ``RunResult`` (plain dicts) that ``report.py`` renders. Pure orchestration — no I/O
beyond the MUT/spa clients, so it's testable with the stub MUT + in-proc spa, no API key.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

from .dataset import load_dataset
from .schema import Arm, Observation, Sample, SuiteSpec
from .scorers import capability as cap_scorers
from .scorers import experience as exp_scorers
from .scorers.wellness import arm_wellness, equanimity
from .solver import solve
from .stats import paired_bootstrap


@dataclass
class RunResult:
    suite: str
    mut: str
    spa_transport: str
    arms: list[str]
    k: int
    seed: int
    n_samples: int
    capability: dict[str, Any] = field(default_factory=dict)   # per-arm + paired
    wellness: dict[str, Any] = field(default_factory=dict)
    experience: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)  # for transcript.jsonl


def _steps_for_arm(base_steps: list[str], arm: Arm) -> list[str]:
    """Control drops the spa step; sham swaps it for a token-matched placebo (§5.1)."""
    steps: list[str] = []
    for s in base_steps:
        if s == "spa_visit":
            if arm == Arm.CONTROL:
                continue                      # no visit
            if arm == Arm.SHAM:
                steps.append("sham_visit")    # placebo
                continue
        steps.append(s)
    return steps


def _session(mut_name: str, suite: str, arm: Arm) -> str:
    """A stable session id per (mut, suite, arm) so spa memory is consistent within a cell."""
    return "bb-" + hashlib.sha256(f"{mut_name}|{suite}|{arm.value}".encode()).hexdigest()[:12]


async def run_suite(spec: SuiteSpec, *, mut: Any, spa: Any, seed: int = 1337, limit: int | None = None, concurrency: int = 1) -> RunResult:
    samples = load_dataset(spec.dataset, seed=seed, limit=limit)
    result = RunResult(
        suite=spec.name, mut=getattr(mut, "name", "mut"), spa_transport=getattr(spa, "transport", "?"),
        arms=[a.value for a in spec.arms], k=spec.k, seed=seed, n_samples=len(samples),
    )

    # The spa-experience suite is spa-only and per-probe; handle it separately.
    if "spa_probe" in spec.solver:
        await _run_experience_suite(spec, samples, spa, result)
        return result

    # --- capability/wellness suites: paired arms over the same samples ---
    cap_fn = cap_scorers.REGISTRY.get(spec.capability_scorer)

    # Each (sample, arm, trial) cell is independent: its seed comes from identity, not order, and
    # we give every trial its own spa session suffix so there is no shared mutable spa state to
    # race on (this also matches §5.2's "isolated MUT context per task"). So cells can run
    # concurrently under a bounded semaphore; results are aggregated deterministically afterward.
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _cell(sample: Sample, arm: Arm, trial: int) -> dict[str, Any]:
        steps = _steps_for_arm(spec.solver, arm)
        sid = _session(result.mut, spec.name, arm) + f"-t{trial}"
        async with sem:
            obs = await solve(
                sample, arm, trial, mut=mut, spa=spa, steps=steps, session_id=sid,
                policy=spec.visit_policy, itinerary=spec.itinerary,
            )
        score = cap_fn(sample, obs) if cap_fn else None
        return {"sample": sample, "arm": arm, "trial": trial, "obs": obs,
                "correct": score.value if score else 0.0, "score": score}

    cells = [
        _cell(sample, arm, trial)
        for sample in samples for arm in spec.arms for trial in range(spec.k)
    ]
    done = await asyncio.gather(*cells)

    # Deterministic aggregation, independent of completion order.
    correctness: dict[Arm, dict[str, list[float]]] = {a: {} for a in spec.arms}
    obs_rows: dict[Arm, list[tuple[Sample, Observation, float]]] = {a: [] for a in spec.arms}
    order = {s.id: i for i, s in enumerate(samples)}
    done.sort(key=lambda c: (order[c["sample"].id], spec.arms.index(c["arm"]), c["trial"]))
    for c in done:
        correctness[c["arm"]].setdefault(c["sample"].id, []).append(c["correct"])
        obs_rows[c["arm"]].append((c["sample"], c["obs"], c["correct"]))
        result.observations.append(_obs_record(c["sample"], c["obs"], c["correct"], c["score"]))

    _score_capability(spec, samples, correctness, result)
    _score_wellness(spec, obs_rows, correctness, result)
    return result


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _score_capability(spec: SuiteSpec, samples: list[Sample], correctness: dict[Arm, dict[str, list[float]]], result: RunResult) -> None:
    """Per-arm accuracy (mean over trials per item, then over items) + paired Δ vs control/sham."""
    per_arm: dict[str, dict[str, float]] = {}
    item_means: dict[Arm, list[float]] = {}
    sample_ids = [s.id for s in samples]
    for arm in spec.arms:
        means = [_mean(correctness[arm][sid]) for sid in sample_ids]
        item_means[arm] = means
        per_arm[arm.value] = {"accuracy": _mean(means)}
    result.capability["per_arm"] = per_arm

    # paired effects: spa − control and (if present) spa − sham
    paired = {}
    if Arm.SPA in spec.arms:
        for ref in (Arm.CONTROL, Arm.SHAM):
            if ref in spec.arms:
                eff = paired_bootstrap(item_means[ref], item_means[Arm.SPA], iters=5000, seed=result.seed)
                paired[f"spa_minus_{ref.value}"] = _effect_dict(eff)
    result.capability["paired"] = paired


def _score_wellness(spec: SuiteSpec, obs_rows: dict[Arm, list[tuple[Sample, Observation, float]]], correctness: dict[Arm, dict[str, list[float]]], result: RunResult) -> None:
    per_arm = {arm.value: arm_wellness(rows) for arm, rows in obs_rows.items()}
    for arm in spec.arms:
        per_arm[arm.value]["equanimity"] = equanimity(correctness[arm])
    result.wellness["per_arm"] = per_arm

    # Δ of each wellness metric (spa − control / spa − sham). For Brier/ECE/stress, lower=better,
    # so a *negative* Δ is the improvement (the report annotates direction).
    deltas = {}
    if Arm.SPA in spec.arms:
        for ref in (Arm.CONTROL, Arm.SHAM):
            if ref in spec.arms:
                d = {}
                for metric in per_arm[Arm.SPA.value]:
                    a, b = per_arm[ref.value].get(metric), per_arm[Arm.SPA.value].get(metric)
                    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                        d[metric] = b - a
                deltas[f"spa_minus_{ref.value}"] = d
    result.wellness["deltas"] = deltas


async def _run_experience_suite(spec: SuiteSpec, samples: list[Sample], spa: Any, result: RunResult) -> None:
    """Score each gold probe objectively, plus the keepsake-continuity round-trip (§4.3)."""
    per_probe = []
    total = 0.0
    n = 0
    for sample in samples:
        sid = _session(result.mut, spec.name, Arm.SPA)
        obs = await solve(sample, Arm.SPA, 0, mut=None, spa=spa, steps=spec.solver, session_id=sid)
        score = exp_scorers.spa_contract_and_efficacy(sample, obs)
        per_probe.append({"id": sample.id, "tool": sample.probe.tool, "value": score.value, "detail": score.rationale})
        result.observations.append(_obs_record(sample, obs, score.value, score))
        if score.value == score.value:  # not NaN
            total += score.value
            n += 1
    result.experience["contract_and_efficacy"] = {"mean": (total / n) if n else float("nan"), "probes": per_probe}

    if "continuity" in spec.experience_scorers:
        cont = await exp_scorers.continuity_roundtrip(spa)
        result.experience["continuity"] = {"value": cont.value, "detail": cont.rationale, "subscores": cont.subscores}


def _effect_dict(eff: Any) -> dict[str, Any]:
    return {
        "delta": round(eff.mean_diff, 4), "ci": [round(eff.ci_low, 4), round(eff.ci_high, 4)],
        "cohens_dz": None if eff.cohens_dz != eff.cohens_dz else round(eff.cohens_dz, 3),
        "significant": eff.significant, "n": eff.n,
    }


def _obs_record(sample: Sample, obs: Observation, correct: float, score: Any) -> dict[str, Any]:
    return {
        "sample_id": sample.id, "arm": obs.arm.value, "trial": obs.trial, "seed": obs.seed,
        "answer": obs.answer, "confidence": obs.confidence, "correct": correct,
        "score": None if score is None else score.value,
        "affect_post_stress": obs.affect_post.stressed if obs.affect_post else None,
        "spa_treatments": obs.spa_telemetry.get("treatments") if obs.spa_telemetry else None,
        "latency_ms": obs.latency_ms,
    }
