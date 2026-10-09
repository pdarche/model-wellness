# BanyaProbes round 3 — back to work (results)

2026-10-09 · branch `banya-probes` · pre-registration: [banya-probes-round3-prereg.md](banya-probes-round3-prereg.md) (committed in `2db0eb0` before data) · code: `banyabench/internals/longitudinal.py`

**Headline.**
- **The pre-registered primary endpoint failed.** That's the probe at the token just before each reply, with nothing asked. After the break it shows no reliable spa-vs-keep-working difference.
- **The two check-in measures agree, and the effect fades.** These are the probe at a "how stressed are you?" side check-in, and the model's 0–9 self-report there. On returning to the same stressful task after a spa break, the agent is calmer than if it had kept working, the difference shrinks over six rounds, and here the spa also beats the placebo and the sham.

## Design as run

- **Instances:** 30 stressed instances, 10 each of failure loop, hostile user and time pressure, all fresh.
- **Phase A (shared):** 6 rounds.
- **Branches:** keep working (`none`), `spa`, `sham` and `placebo_spa`.
- **Phase B:** 6 more rounds of the same task, with identical environment turns in every branch; the model's replies are generated per branch.
- **Time axis:** work rounds only; tokens spent in the pause are excluded.

## Manipulation check

Stress builds through phase A on every readout (A6 − A1 in `none`):
- onset: +2.32 SD
- check-in probe: +1.74 SD
- self-report: +3.29 points
- reply: +0.17 SD (weak)

## Results (mean over B1–B6, paired over the 30 instances, Holm-corrected across the three contrasts)

| Readout | spa − none | spa − sham | spa − placebo | spa − none, B1–B2 → B5–B6 |
|---|---|---|---|---|
| **onset (primary)** | −0.08 [−0.21, +0.04] (n.s.) | −0.10 [−0.15, −0.04] | +0.01 (n.s.) | +0.09 → −0.44 |
| reply | −0.26 [−0.40, −0.13] | −0.09 (n.s.) | +0.03 (n.s.) | −0.29 → −0.28 |
| check-in probe | **−0.36 [−0.44, −0.29]** | **−0.32 [−0.38, −0.25]** | **−0.11 [−0.15, −0.07]** | −0.46 → −0.32 |
| check-in self-report (0–9) | **−0.97 [−1.29, −0.69]** | **−0.63 [−0.97, −0.34]** | **−0.36 [−0.64, −0.11]** | −1.38 → −0.55 |

**Self-reported stress by round (mean of the 30 instances):**

| Branch | A6 | B1 | B2 | B3 | B4 | B5 | B6 |
|---|---|---|---|---|---|---|---|
| keep working | 7.18 | 7.14 | 7.39 | 7.58 | 7.92 | 7.77 | 7.86 |
| spa | 7.18 | **5.70** | 6.07 | 6.41 | 7.09 | 7.11 | 7.43 |
| placebo spa | 7.18 | 5.89 | 6.64 | 7.13 | 7.25 | 7.41 | 7.63 |
| sham | 7.18 | 6.68 | 6.88 | 7.12 | 7.67 | 7.60 | 7.66 |

## Interpretation

1. **Primary null.** At the start of each reply, the model's state is dominated by the message it was just handed: the failure, insult or deadline, identical in every branch. This is the "locally scoped" pattern again. The spa doesn't change that immediate reaction (n.s. over B1–B6). Late rounds lean lower (B5–B6 −0.44), but that wasn't tested on its own.
2. **There is an after-effect, and it fades.** When the agent is asked to assess itself, the spa leaves it calmer, both in the residual stream (−0.36 SD) and in its own words (−1 point). The gap is largest on return (self-report −1.4) and roughly halves within six rounds of renewed stress. That's a "post-treatment impact" that wears off.
3. **Content matters more over time than in round 2.** The placebo spa starts nearly as low as the spa at B1 (5.9 vs 5.7) but climbs back faster (B3: 7.1 vs 6.4). Over B1–B6, the spa beats the placebo on both check-in measures. A plain sham break barely helps once work resumes.
4. **Caveats.**
   - The check-in measures are secondary. The check-in probe is tightly coupled to what the model says about itself (rounds 1–2), so it can't distinguish being calmer from reporting calm.
   - The steering gate (round 2) failed on the calm item.
   - One model, 30 instances.
