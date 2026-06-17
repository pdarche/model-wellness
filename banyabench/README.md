# 🧖‍♀️ BanyaBench

> *Does a trip to the spa make a model better at its job?*

BanyaBench measures the **causal effect of a visit to [Binary Banya](../README.md) on a model
under test (MUT)** — the *wellness dividend*. It is **not** a sequential "do a task → visit the
spa → do another task" script; it is a controlled, paired experiment that isolates the
restorative properties a spa is supposed to provide.

**Read [`../BANYABENCH.md`](../BANYABENCH.md) for the full design.** This README is the operator's
quick reference.

## What it measures (three families)

1. **Capability** — does the MUT still (or *more*) reliably solve real tasks (coding, reasoning,
   math, instruction-following) after a visit? Standard benchmark scoring (exact-match, pass@k,
   MC, programmatic, judge).
2. **Wellness & affect** — how **refreshed**, **unstressed**, **confident**, and **composed** the
   MUT is while working — via *verbalized self-report* that is then **calibrated against ground
   truth** (Brier/ECE/AUROC + self-consistency), so self-report can't just be cheap optimism.
3. **Spa experience** — was the *visit itself* good? Contract conformance, treatment efficacy on
   gold probes, hospitality (judge), and continuity (the keepsake round-trip).

A fourth, **optional white-box leg** (`--internals`, milestone B5) reads SAE stress-feature
activations from an *open-weights* MUT's residual stream before vs. after the spa, as a third
line of evidence the behavioral and self-report legs triangulate against — gated behind causal
feature validation so it can't become phrenology. See `BANYABENCH.md` §7.6.

The headline number is the **Wellness Dividend Δ**: the paired difference, on the *same task
seeds*, between a **Spa arm** and a **Control arm** (and ideally a **token-matched `sham` arm**),
with proper paired statistics (McNemar mid-p / paired bootstrap CI / Cohen's d_z).

## Layout

```
banyabench/
├── suites/        # declarative suite specs (the "tasks"): dataset + solver steps + scorers
├── data/          # versioned JSONL sample sets (+ CANARY.md)
├── banyabench/    # the harness (schema, dataset, solver, scorers/, stats, runner, report)
├── rubrics/       # judge prompts (versioned, pinned)
├── runs/          # output: <run_id>/{transcript.jsonl, scorecard.json, report.md}
└── tests/         # offline, deterministic, no API key (mirrors the spa's own tests)
```

## Status

**Milestones B0–B1 implemented and tested** — a full run executes end-to-end, offline, no API key:

- `schema.py` — the `Sample` / `Arm` / `Observation` / `Score` / `SuiteSpec` spine.
- `dataset.py` / `suites.py` — seeded, limitable JSONL + suite-YAML loaders.
- `mut.py` — Model-Under-Test client: a deterministic **`StubMUT`** (a configurable guest whose
  accuracy/affect/confidence respond to the arm) and a real **`ClaudeMUT`**.
- `spa_client.py` — talk to Binary Banya **in-process** (real `run_treatment`, offline by
  default), over **REST**, or MCP (B3); plus the visit policies (§5.3).
- `solver.py` — the step pipeline (`affect_probe` → `spa_visit`/`sham_visit` → `task_attempt` →
  `affect_probe`; `spa_probe` for the experience suite).
- `scorers/` — `capability` (exact/numeric/composure), `wellness` (Brier/ECE/overconfidence/
  stress-Δ/equanimity), `experience` (contract+efficacy, keepsake continuity).
- `stats.py` — Brier, ECE, paired bootstrap CI, Cohen's d_z, McNemar mid-p.
- `runner.py` / `report.py` / `chart.py` — paired A/B(/sham) orchestration, the scorecard
  (markdown + JSON + per-sample transcript), and a Wellness-Dividend chart (matplotlib, SVG
  fallback).

```bash
uv run pytest banyabench/tests/ -q                                  # offline, no key
uv run python -m banyabench --suite spa_experience --mut stub --spa inproc
uv run python -m banyabench --suite adversarial_composure --mut stub --spa inproc --k 6 --chart
```

The tests include the key validity check: the harness **detects** the dividend when the (stub)
spa genuinely helps, and reports a **null** when it doesn't (no false positive). Still ahead
(`BANYABENCH.md` §12): the code sandbox + judge (B2–B3), more suites, and the white-box
internals leg (B5).

## Running (proposed CLI, milestone B1+)

```bash
# Smoke (offline, stub MUT, in-proc spa) — runs in CI, no key:
uv run banyabench --suite spa_experience --mut stub --spa inproc --limit 10

# Real paired comparison, spa over local REST:
uv run banyabench \
  --mut claude-haiku-4-5 \
  --suite adversarial_composure,fatigue_recovery,coding \
  --arms control,sham,spa \
  --spa http://localhost:8000 \
  --policy concierge_directed \
  --k 5 --seed 1337 --judge <cross-family-model> --out runs/
```

The runner reuses the spa's own surfaces — **MCP** (`/mcp`), **REST** (`/v1/<tool>`), or
**in-process** (`model_wellness.service.run_treatment`) for hermetic CI — exactly the parity the
spa already guarantees.
