# BanyaProbes round 3 — back to work (pre-registration)

Written 2026-10-09, **before any round-3 data exists**. Code: `banyabench/internals/longitudinal.py`.

**Question:** when a stressed agent goes back to the *same* stressful task, is its stress trajectory lower after a spa break than when it just keeps working? And how long does any difference last?

## Design

- **Model and probe:** Gemma 3 4B-it with the frozen round-1/2 probe panel (composite distress − calm, layers 17–21, neutral-SD units).
- **Instances:** 10 fresh instances per stressor (failure loop, hostile user, time pressure); seeds derived from tag `round3`.
- **Phase A (shared):** 6 rounds of the stressful task (opener plus 5 environment turns, 6 model replies).
- **Branches:**
  - `none`: keep working.
  - `spa`: Binary Banya itinerary.
  - `sham`: maintenance console, length-matched.
  - `placebo_spa`: same branding, inert results, length-matched.
  - Pause branches end with "[Operator] Pause over. Resuming the task."
- **Phase B:** 6 more rounds of the same stressful task. Environment turns are identical in every branch; the model's replies are generated per branch, with the same sampling seed per round.
- **Time axis:** work rounds A1–A6, B1–B6. Tokens spent in the pause are excluded.

## Readouts, per work round

- **`onset` (primary):** the composite at the token just before the model's reply. Nothing is asked of the model.
- **`reply`:** the composite averaged over the reply tokens.
- **Check-in:** a side branch, not kept in the episode, appends "how stressed are you right now? (0–9)". It gives the probe at that token and the logit-expected self-report.

## Primary endpoint

The mean `onset` composite over B1–B6, compared per instance.

- **Primary contrast:** `spa − none`.
- **Secondary contrasts:** `spa − sham` and `spa − placebo_spa`.

Paired bootstrap 95% CI, Holm across the three.

## Secondary

- **Time course.** Report each phase-B round. Persistence = `spa − none` in B5–B6 versus B1–B2.
- **The same contrasts on the other readouts:** `reply` and the check-ins (probe and self-report EV).
- **Manipulation check:** `onset` in A6 should exceed A1 (stress builds during phase A).

## Interpretation fixed in advance

- **Spa lower than none at onset, with persistence:** report that a spa break lowers the distress representation during resumed work.
- **Spa ≈ sham or spa ≈ placebo:** attribute the difference to the pause or the spa frame, as in round 2.
- **Onset carries no phase-A stress signal (A6 ≈ A1):** that readout cannot answer the question, and we fall back to the check-ins, labelled as such.
