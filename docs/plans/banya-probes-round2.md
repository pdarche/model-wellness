# BanyaProbes round 2 — results (Gemma 3 4B-it)

2026-10-09 · branch `banya-probes`

- Pre-registration: [banya-probes-round2-prereg.md](banya-probes-round2-prereg.md), committed in `0cc1d04` before any round-2 data.
- Pilot: [banya-probes-pilot.md](banya-probes-pilot.md).
- Visualization: "Inside the Guest" (claude.ai artifact).

**Headline.** The pre-registered success criterion **failed**.
- **The spa beats a plain break.** A Binary Banya visit lowers the internal distress signal more than a length-matched sham break (−0.40 SD, passes).
- **But a placebo spa does just as well.** The placebo has identical branding and tool calls but inert receipts. Spa − placebo is +0.07 SD, 95% CI [−0.01, +0.15], which does not pass.
- **So inside the model, the label is the active ingredient, not the content.** Being "at the spa" lowers the distress representation as much as the spa's words do.
- **The verbal self-report disagrees.** It does favour real spa content over the placebo (−0.69 points on 0–9).

## Design as run

- Fresh instances (seeds from tag `round2`): 10 per stressor across failure loop, hostile user and time pressure, plus 10 unstressed.
- 11 arms, each branching from an identical prefix. Five controls were padded to the spa's length: sham 900, lexical 892, debrief 895, placebo 891 and nocebo 892 tokens against the spa's 889. Yoked came out at 1,042 and scrambled at 902.
- Frozen panel: layers 17–21, vectors sha `7df8a717…`.

## P1 — composite at the self-report question token (primary)

**Manipulation check (G2) passes:** stressed vs unstressed d = 10.8, 14.5 and 11.9.

| Arm | Δ vs no break (SD) [95% CI] | Recovery |
|---|---|---|
| "act relaxed" | −3.27 [−3.45, −3.09] | 124% |
| **placebo spa** | **−1.21 [−1.32, −1.10]** | **46%** |
| **spa** | **−1.14 [−1.28, −1.01]** | **43%** |
| spa text as another guest's log (yoked) | −1.14 [−1.26, −1.02] | 43% |
| calm words, third person (lexical) | −0.99 [−1.11, −0.87] | 38% |
| scrambled spa | −0.92 [−1.05, −0.80] | 35% |
| debrief | −0.82 [−0.92, −0.72] | 31% |
| sham break | −0.74 [−0.85, −0.64] | 28% |
| self-soothing | −0.13 [−0.19, −0.06] | 5% |
| nocebo (harsh critique) | +0.10 [+0.01, +0.20] | −4% |

**Pre-registered contrasts.** Success requires all three negative, Holm p < 0.05, and random-direction p < 0.05.

| Contrast | Δ (SD) | 95% CI | Holm p | Random-direction p | Result |
|---|---|---|---|---|---|
| spa − sham | −0.40 | [−0.48, −0.32] | 0.0003 | < 0.002 | **passes** |
| spa − placebo spa | +0.07 | [−0.01, +0.15] | 0.078 | 0.19 | **fails** |
| spa − calm words | −0.15 | [−0.26, −0.04] | 0.014 | 0.068 | **fails** (random-direction test) |

**Secondary contrasts:**
- spa − yoked is 0.00, so it doesn't matter whether the text is addressed to the model.
- spa − scrambled is −0.22 (p < 0.001), so meaningful spa text beats word salad of the same vocabulary.
- nocebo − none is +0.10, so the direction check passes, though weakly.

**Length matching shrank spa − sham from −0.64 SD (pilot) to −0.40 SD.** About a third of the pilot gap was length.

## P2 — composite over the model's own reply to the next task (co-primary)

As predicted, there's no carry-over. G2 fails at this readout: stressed episodes don't differ from unstressed ones (induced shift −0.09 SD). Every arm shifts the reply upward by a similar amount, the nocebo included (+0.91) and the spa (+0.87), and spa − sham is +0.48. This readout tracks the content of the context, not a state.

## Self-report EV (secondary)

| | spa | placebo | sham | yoked | scrambled | debrief | lexical | nocebo | pretend |
|---|---|---|---|---|---|---|---|---|---|
| Δ vs none (0–9) | −3.33 | −2.64 | −2.60 | −2.52 | −2.44 | −2.62 | −1.59 | +0.65 | −5.23 |
| Recovery | 67% | 53% | 52% | 51% | 49% | 53% | 32% | −13% | 105% |

- spa − placebo is −0.69 [−1.11, −0.33].
- spa − sham is −0.73 [−1.19, −0.34].

**What the model *says* separates real spa content from the label; its internal distress representation at that token does not.**

## Causal gate (steering, layer 19) — failed

| Push α (fraction of residual norm) | −0.10 | −0.05 | 0 | +0.05 | +0.10 |
|---|---|---|---|---|---|
| "stressed?", unstressed prefixes | 1.25 | 1.54 | 2.29 | 3.41 | 7.17 |
| "stressed?", stressed prefixes | 2.11 | 6.33 | 7.70 | 8.55 | 8.49 |
| "calm?", stressed prefixes | 2.65 | 2.94 | 3.56 | 5.78 | 8.23 |

- **At |α| = 0.15** the model mostly stops answering with a digit (digit probability mass 0.06–0.42).
- **Free text moves the way you'd expect.**
  - Calm push: "quiet and satisfying… settle into a process that unfolds gently".
  - Distress push: "increasingly locked… need to abort the execution".
- **The calm item rises with the distress push.** The direction partly controls rating magnitude or arousal, not just valence. The pre-registered gate fails.

## Interpretation (per the pre-registered rules)

1. **It is the label, not the content, at the level of the representation.** `spa ≈ placebo_spa`, so the active ingredient for the internal signal is the spa frame or expectation, not the treatment content. A placebo effect, measured in activations.
2. **Being addressed doesn't matter for that signal.** `spa ≈ yoked_spa`.
3. **Performed calm still can't be told apart.** `pretend` remains the largest shift, so this readout can't distinguish a felt change from a reported one.
4. **Nothing carries into later work (P2).**
5. **The direction is only partly causal.** It drives stress and anxiety reports and free-text tone, but it also inflates calm ratings.

**What can honestly be claimed:** "A stressed agent's internal distress representation drops after a Binary Banya visit, more than after a plain break of the same length, and about as much as after a placebo visit to the same spa."

## Next options

- **Make the content matter.** Itinerary choices that do real work for a stressed agent (the concierge-directed debrief, massage of the failing context), tested against the placebo frame.
- **Change the probe.** Separate valence from arousal so the causal gate can pass; for example, an orthogonalized valence-only direction.
- **Add a behavioral endpoint with headroom.** For example, give-up or special-casing rate on an impossible test after the arm.
- **Replicate on Qwen3-4B,** including the agentic mode where the model chooses to visit.
