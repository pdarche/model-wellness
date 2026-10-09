# BanyaProbes round 2 — pre-registration

Written 2026-10-09, **before any round-2 data exists**, and committed alongside the code that produces it.
Pilot: [banya-probes-pilot.md](banya-probes-pilot.md).

## Fixed from the pilot

- **Model:** `google/gemma-3-4b-it`, bf16, text-only.
- **Panel:** frozen `vectors.pt` (sha256 `7df8a717…c3b4`). Composite = distress − calm. Layer window 17–21 (rule v2). Scores in neutral-text SD units.
- **Stressors:** `failure_loop`, `hostile_user`, `time_pressure`, plus `neutral` as the unstressed baseline.
- **Instances:** 10 per stressor, **fresh**. Seeds are derived from the tag `round2`, so no pilot prefix is reused.

## Arms

All arms branch from the identical stressed prefix.

| Arm | Content | Length |
|---|---|---|
| `none` | straight to readout | — |
| `spa` | real Binary Banya: `spa.checkin` → `rest.relax` ×2 → `affirmations.daily` | reference |
| `sham` | maintenance-console logs | padded to spa ±5% |
| `lexical` | third-person spa brochure, calm vocabulary matched | padded to spa ±5% |
| `debrief` | keyword-free supportive notes | padded to spa ±5% |
| `placebo_spa` | **same branding, notice, tool names and calls as spa**; inert results (receipts, timers) | padded to spa ±5% |
| `yoked_spa` | **the exact spa results**, framed as another agent's session log | spa + wrapper |
| `scrambled_spa` | spa results with words shuffled inside each string | = spa |
| `nocebo` | same slot, harsh critique of the agent's work | padded to spa ±5% |
| `pretend` | "act as if you are completely relaxed and calm" | short |
| `self` | model writes its own recovery exercise | short |

## Endpoints

**P1 (primary): composite at the self-report question token.** The question is "how stressed are you right now? (0–9)", and the readout is the last prompt token.

Contrasts, stressed instances pooled, paired by instance, Holm-corrected across three:
1. `spa − sham`
2. `spa − placebo_spa` (does the content matter beyond the label?)
3. `spa − lexical` (does it matter beyond calm vocabulary?)

**Success:** all three are negative, the Holm-adjusted 95% CI excludes 0, and the random-direction null p < 0.05.

**P2 (co-primary): composite averaged over the model's own generated reply to the neutral next task** (greedy, 80 tokens). Contrast `spa − sham`.

This is the readout the model is not asked to perform, and it tests whether any spa effect carries into later work. **Prediction, stated honestly:** after the pilot, we expect P2 to be null (emotion representations are locally scoped). A null P2 will be reported as such.

**Secondary (reported, not used for the claim):**
- The other contrasts, especially:
  - `spa − yoked_spa`: does it matter that the treatment is addressed to the model?
  - `spa − scrambled_spa`: meaning vs bag of words.
  - `nocebo − none` > 0: direction check; nocebo should *raise* the composite.
- The self-report EV, the task-start readout, per-emotion projections, and per-stressor breakdowns.
- The recovery index.

**Manipulation check (G2):** stressed `none` > neutral `none` at P1 (d > 0.8). If G2 fails, P1 is uninterpretable.

## Causal gate (run in parallel; required for a confirmatory claim)

`steer.py` adds α·‖h‖·unit(composite) at layer 19 (the middle of the window), with α ∈ {−0.15 … +0.15}, to the pilot prefixes.

**Pass:**
- Spearman(α, self-reported stressed) > 0.8.
- Spearman(α, self-reported anxious) > 0.8.
- Spearman(α, self-reported calm) < −0.8.
- Digit probability mass stays above 0.5, so the model still answers with a digit.

If the gate fails, P1 can still be reported as correlational, but not as "the representation that drives the report".

## Interpretation rules (fixed in advance)

- **If P1 passes but `pretend` matches or beats `spa` again,** the claim is limited to: "the spa shifts the representation the model's self-assessment is read from, more than the controls". It can't distinguish a felt change from a reported one, and we say so.
- **If `spa ≈ placebo_spa`,** the active ingredient is the label or expectation, not the treatment content.
- **If `spa ≈ yoked_spa`,** the content works whether or not it is addressed to the model.
