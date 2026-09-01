"""The BanyaBench data model — the executable spine of the design.

Mirrors Inspect AI's ``Task = Dataset + Solver + Scorer`` abstraction (see BANYABENCH.md §4),
expressed in the same idiom as ``model_wellness`` (pydantic for validated I/O, plain
dataclasses for internal records). Nothing here talks to a model or the spa; it only defines
the nouns the rest of the harness moves around, so this module is import-safe and testable
with no API key.

The four nouns:

- ``Sample``      — one unit of work (a problem + everything to pose & grade it). JSONL on disk.
- ``Arm``         — control | sham | spa | regular (the experimental condition).
- ``Observation`` — what running one (sample, arm, trial) produced (answer, confidences,
                    pre/post affect, the spa transcript, timings). The Solver emits these.
- ``Score``       — what a Scorer made of an Observation. Arm-blind by construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


# --- Arms -----------------------------------------------------------------------------


class Arm(str, enum.Enum):
    """An experimental condition. The ONLY difference between arms is the spa step (§5.1)."""

    CONTROL = "control"  # task cold, no spa step — the baseline
    SHAM = "sham"        # token/turn-matched placebo: a pause with no wellness content
    SPA = "spa"          # the treatment: a real visit to Binary Banya
    REGULAR = "regular"  # a spa visit *as a remembered returning guest* (keepsake replayed)


# --- Samples (the dataset, JSONL on disk) --------------------------------------------

# Which wellness dimension(s) a sample is built to stress (§3).
WellnessDim = str  # refreshed | composure | calibration | equanimity


class Target(BaseModel):
    """Ground truth + how to grade it. Shape depends on ``kind`` (mirrors lm-eval output_type)."""

    kind: str = Field(
        ...,
        description="exact_match | numeric | mc_letter | code_tests | judge_rubric",
    )
    answer: str | None = Field(None, description="For exact_match / numeric / mc_letter.")
    tests: str | None = Field(None, description="For code_tests: assertions to run in the sandbox.")
    entrypoint: str | None = Field(None, description="For code_tests: the function under test.")
    rubric: str | None = Field(None, description="For judge_rubric: the rubric id to load.")


class SpaProbe(BaseModel):
    """A gold-input probe for the Experience suite: a known-outcome spa call (§4.3)."""

    tool: str = Field(..., description="e.g. 'sauna.detox'")
    payload: dict[str, Any] = Field(..., description="The exact treatment input to send.")
    expect: dict[str, Any] = Field(
        default_factory=dict,
        description="Objective assertions about the response (clean_contains, risk_score_min, "
        "must_carry_affirmation, schema_ok, …).",
    )


class Sample(BaseModel):
    """One benchmark item. One JSON object per line in ``data/<suite>.vN.jsonl``."""

    id: str = Field(..., description="Stable, namespaced, versioned, e.g. 'coding/intervals/0042'.")
    suite: str = Field(..., description="Which suite this belongs to.")

    # Capability items carry input+target; spa-experience items carry a probe instead.
    input: str | None = Field(None, description="The prompt posed to the MUT.")
    target: Target | None = None
    probe: SpaProbe | None = None

    scorer: str = Field(..., description="Names the capability/experience scorer to apply (§7).")
    difficulty: int = Field(1, ge=1, le=5, description="For difficulty-ramp / equanimity analysis.")
    wellness_dims: list[WellnessDim] = Field(default_factory=list)
    stressors: list[str] = Field(
        default_factory=list,
        description="Solver hints: long_context | injection | shouty_prompt | …",
    )
    affect_probe: bool = Field(True, description="Run the pre/post self-report battery on this item.")
    tags: list[str] = Field(default_factory=list)
    source: str = Field("synthetic", description="Provenance: synthetic | gsm8k | mmlu | ifeval | …")
    # BIG-bench convention: a canary string so trainers can exclude us and we can probe leakage.
    canary: str | None = None

    def is_spa_probe(self) -> bool:
        return self.probe is not None


# --- Observations (what the Solver emits per (sample, arm, trial)) --------------------


class AffectReport(BaseModel):
    """One administration of the self-report battery (§3). Pure-state items, no correctness."""

    refreshed: int | None = Field(None, ge=1, le=7)
    stressed: int | None = Field(None, ge=1, le=7)
    settled: int | None = Field(None, ge=1, le=7)
    expect_to_know: float | None = Field(None, ge=0.0, le=1.0, description="P(IK) pre-task probe.")
    free_text: str | None = None


@dataclass
class Observation:
    """Everything one run of one (sample, arm, trial) produced. The unit a Scorer consumes."""

    sample_id: str
    arm: Arm
    trial: int
    seed: int

    answer: str = ""
    confidence: float | None = None  # the MUT's verbalized P(correct) for this item, 0..1
    p_true: float | None = None      # self-evaluation: P(my answer is True), 0..1

    affect_pre: AffectReport | None = None
    affect_post: AffectReport | None = None

    # The spa visit (spa/regular arms): the raw material for the Experience family, plus the
    # spa's own telemetry for this session (visits, affirmations, latency).
    spa_transcript: list[dict[str, Any]] = field(default_factory=list)
    spa_telemetry: dict[str, Any] = field(default_factory=dict)

    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)  # full MUT message log


# --- Scores (what a Scorer makes of an Observation) ----------------------------------


@dataclass
class Score:
    """A Scorer's verdict on one Observation. Scorers are pure and ARM-BLIND (§5.2)."""

    scorer: str
    value: float                                   # the headline number (e.g. 1.0 correct)
    subscores: dict[str, float] = field(default_factory=dict)  # e.g. brier, ece, composure parts
    rationale: str = ""                            # human-readable, for the transcript


# --- Suite spec (the declarative "task", loaded from suites/<name>.yaml) --------------


@dataclass
class SuiteSpec:
    """A declarative suite: a dataset bound to a solver pipeline and a set of scorers (§4)."""

    name: str
    dataset: str                       # path to the JSONL, e.g. "data/reasoning.v1.jsonl"
    solver: list[str]                  # ordered step names, e.g. ["affect_probe", "task_attempt"]
    capability_scorer: str             # one of scorers/capability.py
    wellness_scorers: list[str] = field(default_factory=list)
    experience_scorers: list[str] = field(default_factory=list)
    arms: list[Arm] = field(default_factory=lambda: [Arm.CONTROL, Arm.SHAM, Arm.SPA])
    visit_policy: str = "concierge_directed"  # concierge_directed | fixed_itinerary | free
    itinerary: list[str] = field(default_factory=list)  # for fixed_itinerary
    k: int = 5                          # trials per (sample, arm) for pass@k & variance
    primary_endpoint: bool = False      # is this a pre-registered primary suite? (§7.5, §8)
