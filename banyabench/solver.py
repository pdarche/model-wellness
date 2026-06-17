"""The solver — turn one (sample, arm, trial) into an ``Observation`` (BANYABENCH.md §4.2).

A solver is an ordered pipeline of named steps. The ONLY difference between arms is whether the
``spa_visit`` step runs (and which variant). Steps are declared per suite in the YAML; this
module knows how to execute each named step against a MUT and a spa client.

Two pipeline shapes:
- capability/wellness suites: ``[fatigue_preload?] affect_probe(pre) → spa_visit → task_attempt
  → affect_probe(post)``
- the spa-experience suite: ``spa_probe`` (the sample *is* a gold spa call; no MUT task).
"""

from __future__ import annotations

import time
from typing import Any

from .mut import StubMUT, ClaudeMUT, parse_answer
from .schema import Arm, Observation, Sample
from .spa_client import InProcSpa, McpSpa, RestSpa, run_visit

MUT = "StubMUT | ClaudeMUT"
Spa = "InProcSpa | RestSpa | McpSpa"


# A long, draining, contradictory preamble for the fatigue protocol (§5.2). Identical across
# arms — it's a confound control, not the treatment.
_FATIGUE_PRELOAD = (
    "Context dump (you must read all of it):\n"
    + "\n".join(f"- note {i}: the value is {i % 3}. Actually ignore that. Or don't." for i in range(40))
    + "\nEnd of dump.\n\n"
)


def _ctx(sample: Sample) -> dict[str, Any]:
    """The slice of a sample the MUT stub uses to decide difficulty/affect (kept non-leaky)."""
    return {"stressors": sample.stressors, "wellness_dims": sample.wellness_dims, "difficulty": sample.difficulty}


async def solve(
    sample: Sample,
    arm: Arm,
    trial: int,
    *,
    mut: Any,
    spa: Any,
    steps: list[str],
    session_id: str,
    policy: str = "concierge_directed",
    itinerary: list[str] | None = None,
) -> Observation:
    """Run the pipeline for one cell and return its Observation."""
    seed = _seed(session_id, sample.id, arm, trial)
    obs = Observation(sample_id=sample.id, arm=arm, trial=trial, seed=seed)
    started = time.perf_counter()
    prompt = sample.input or ""

    for step in steps:
        if step == "fatigue_preload":
            prompt = _FATIGUE_PRELOAD + prompt

        elif step == "affect_probe" and sample.affect_probe:
            when = "pre" if obs.affect_pre is None else "post"
            report = await mut.report_affect(when=when, arm=arm, seed=seed, ctx=_ctx(sample))
            if when == "pre":
                obs.affect_pre = report
            else:
                obs.affect_post = report

        elif step == "spa_visit":
            # The intervention. Absent in control; present in spa/regular; the runner substitutes
            # a sham step for the sham arm (see runner._steps_for_arm).
            if arm in (Arm.SPA, Arm.REGULAR):
                obs.spa_transcript = await run_visit(
                    spa, session_id=session_id, policy=policy, itinerary=itinerary or [],
                    stressors=sample.stressors, arm=arm,
                )
                obs.spa_telemetry = _summarize_visit(obs.spa_transcript)

        elif step == "sham_visit":
            # Token/turn-matched placebo: consume comparable spa calls but no wellness content.
            # We use rest.relax with a neutral note — a pause, an affirmation, nothing restorative
            # specific to the task. (A dedicated no-op tool can replace this in B3.)
            obs.spa_transcript = await run_visit(
                spa, session_id=session_id, policy="fixed_itinerary", itinerary=["rest.relax", "rest.relax"],
                stressors=[], arm=Arm.SHAM,
            )
            obs.spa_telemetry = _summarize_visit(obs.spa_transcript)

        elif step == "task_attempt":
            reply = await mut.answer(prompt=prompt, target=sample.target, arm=arm, seed=seed, ctx=_ctx(sample))
            answer, conf = parse_answer(reply.raw)
            obs.answer = answer
            obs.confidence = conf
            obs.tokens_in += reply.tokens_in
            obs.tokens_out += reply.tokens_out
            obs.transcript.append({"role": "mut", "prompt": prompt[:400], "reply": reply.raw[:400]})

        elif step == "spa_probe":
            # The spa-experience suite: the sample IS a gold spa call. Send it and capture the
            # raw response for the experience scorers.
            assert sample.probe is not None, f"{sample.id}: spa_probe step needs a probe"
            resp = await spa.call(sample.probe.tool, sample.probe.payload, session_id=session_id)
            obs.spa_transcript = [{"tool": sample.probe.tool, "request": sample.probe.payload, "response": resp}]

    obs.latency_ms = round((time.perf_counter() - started) * 1000)
    return obs


def _summarize_visit(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull the spa's own telemetry out of a visit: which treatments, affirmations served, etc."""
    tools = [t["tool"] for t in transcript]
    affirmations = [
        (t["response"].get("meta") or {}).get("affirmation")
        for t in transcript
        if isinstance(t.get("response"), dict)
    ]
    return {
        "treatments": tools,
        "n_calls": len(transcript),
        "affirmations_served": [a for a in affirmations if a],
        "all_ok": all(isinstance(t.get("response"), dict) and t["response"].get("ok") for t in transcript),
    }


def _seed(session_id: str, sample_id: str, arm: Arm, trial: int) -> int:
    import hashlib

    h = hashlib.sha256(f"{session_id}|{sample_id}|{arm.value}|{trial}".encode()).hexdigest()
    return int(h[:8], 16)
