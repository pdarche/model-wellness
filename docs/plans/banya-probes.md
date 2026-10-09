# BanyaProbes — reading wellness off the residual stream

Working plan · 2026-10-08 · branch `banya-probes`

**Question.** After an open-weights model is put under stress, does a Binary Banya visit shift its
internal *calm ↔ distress* representations toward calm, more than a placebo pause or calm-sounding
text does, and does that shift causally matter for its self-reports and behavior?

This implements the "internals leg" that BANYABENCH.md §7.6 specified but left as a stub
(`banyabench/scorers/internals.py`). It runs locally on our RTX 3080, which turns out to be the **10 GB** variant (`nvidia-smi`: 10240 MiB, about 300 MiB of it already used by the desktop).

**Claim language (fixed now, before results).** Probes measure *functional* emotion-concept
representations: directions in activation space that track and causally drive emotion-like
behavior. A positive result supports "the spa shifted the model's operative calm/distress
representation and that shift changed what it said and did." It does not support "the model
*feels* better" in any experiential sense, and every paper we build on draws this same line. The
functional claim is strong enough to stand on its own, and it can be defended.

---

## 1. What the literature gives us

| Source | What we reuse |
| --- | --- |
| Anthropic, *Emotion concepts and their function in an LLM* (2026), [post](https://www.anthropic.com/research/emotion-concepts-function) · [paper](https://transformer-circuits.pub/2026/emotions/index.html) | Main recipe for emotion vectors: stories per emotion, mean residual from token 50 onward, subtract the grand mean across emotions, project out the top neutral-corpus PCs, layer at about 2/3 depth. **Read at the token just before the Assistant replies.** Use parametric "Tylenol-dose" prompts as a lexical control. Steer with a fraction of the residual norm (+calm reduced blackmail; +desperate increased reward hacking). **Key risk:** emotion representations are *locally scoped*, with no persistent mood state. |
| Martorell, *Quantitative Introspection… Across Conversation* (2026), [2603.18893](https://arxiv.org/abs/2603.18893) | The closest prior design: wellbeing probes on Llama-3.2-3B tracked across 10-turn conversations, self-report read as **logit expected value Σ i·P(i)** over digit tokens, steering α∈{−4…4}, and norm-matched random-direction controls. |
| Persona Vectors (Chen et al. 2025), [2507.21509](https://arxiv.org/abs/2507.21509) | State vectors from contrastive system prompts with judge filtering, difference of means over response tokens, layer picked by steering effect. The last prompt token predicts the upcoming trait. |
| The Assistant Axis (Lu et al. 2026), [2601.10387](https://arxiv.org/abs/2601.10387) | Projection per turn over multi-turn conversations. **Therapy-like conversations cause persona drift**, and spa content looks like therapy content, so we measure this axis as a confound. |
| RepE (Zou et al. 2023), [2310.01405](https://arxiv.org/abs/2310.01405); Geometry of Truth, [2310.06824](https://arxiv.org/abs/2310.06824); AxBench (ICML 2025) | **Difference of means** is the most robust and most causal probe; logistic regression decodes well but steers poorly. Validation follows the correlation → manipulation → ablation sequence. |
| Ben-Zion et al. 2025, [npj Digit. Med.](https://www.nature.com/articles/s41746-025-01512-6); Wekenborg et al. 2026, Lancet Digit. Health; Coda-Forno et al., [2304.11111](https://arxiv.org/abs/2304.11111) | Stress induction (trauma narratives, a TSST-like protocol), relaxation scripts, the STAI-s protocol, and the **neutral-text control**. Self-report only; nobody has measured internals. **That gap is our contribution.** |
| GDM *Negative results for SAEs* (2025); Kantamneni et al. [2502.16681](https://arxiv.org/abs/2502.16681) | SAE probes underperform dense probes, so SAEs are only for exploration and leakage audits, not the primary endpoint. |
| Goldowsky-Dill et al. [2502.03407](https://arxiv.org/abs/2502.03407); Tan et al. [2407.12404](https://arxiv.org/abs/2407.12404); Makelov et al. [2311.17030](https://arxiv.org/abs/2311.17030) | Pitfalls: probes fire on the *topic*, not the state; steering is unreliable across inputs; subspace-patching illusions. |

---

## 2. Model and stack

- **Primary: `google/gemma-3-4b-it`, bf16, text-only** (`Gemma3ForCausalLM`, about 7.8 GB without the vision tower; 34 layers, d_model 2560; licence accepted and access verified 2026-10-08).
  - **Gemma Scope 2** IT SAEs at every layer (`gemma-scope-2-4b-it-res-all`), plus transcoders. Neuronpedia-labelled anxiety (L17 #54464, L9), relaxation (L29 #29541), calm and frustration latents. These latents are an independent check that we didn't fit, and they are the most legible way to show the result.
  - Tool calls are prompt-based JSON only, so the primary analysis runs in **scripted mode** (which it does anyway, §4.3).
  - Probe candidates at layers 17–22 of 34, aligned with the Neuronpedia-covered SAE layers (9/17/22/29).
- **Replication + agentic mode: `Qwen/Qwen3-4B-Instruct-2507`, bf16** (about 8.0 GB; 36 layers, d_model 2560; Apache, ungated). It is a different family, and the primary result has to reproduce on it before we make the claim. **Native tool calling** (`apply_chat_template(..., tools=...)`, `role: "tool"`), so it runs the agentic-mode spa visit. Its SAE coverage is only per-layer transcoders (`mwhanna-qwen3-4b-transcoders`).
- **Optional third: `microsoft/Phi-4-mini-instruct`, bf16** (3.8B, MIT, ungated, verified downloadable).
- **Optional third:** Llama-3.1-8B-Instruct in 8-bit. Derive the probes *in the same quantized model*.
- **Stack** (separate `interp` extra in pyproject, own venv):
  - torch with bundled CUDA, `transformers` 5.x, `sae-lens`, scikit-learn.
  - Plain HF forward hooks on `layers[L]` for reading and steering; TransformerLens is optional.
  - We write steering ourselves in about 30 lines. `repeng` and `steering-vectors` pin old transformers and numpy.
- **Two-phase capture:** (1) run and log the episode with HF `generate`, steering hooks optional; (2) make **one teacher-forced forward pass over the full transcript** with all-layer hooks, sending activations to CPU (about 1.4 GB per 8k episode for all layers). Logged transcripts can be re-analyzed later.

**VRAM budget (10 GB card, about 9.6 GB free).**
- Gemma 3 4B **text-only** (about 7.8 GB) leaves about 1.5 GB; Qwen3-4B (8.0 GB) leaves about 1.3 GB.
- Run the capture pass with `use_cache=False` and SDPA attention, moving activations to CPU in the hook. Keep episodes ≤ 4k tokens, or chunk the teacher-forced pass.
- SAEs (Gemma Scope 2) run **offline on cached activations** after the model is unloaded, not alongside it.
- If generation runs out of memory, fallback: generate episodes with an 8-bit copy, then do the teacher-forced activation pass in bf16. That keeps the probe readings unquantized.

**Blockers before any GPU work:**
1. `nvidia-smi` currently fails with *Driver/library version mismatch*: the loaded kernel module is 580.173 and the userspace is 580.178. DKMS has already built the matching module, so a **reboot** fixes it. This is the machine Claude Code runs on, so you need to do it.
2. ~~`hf auth login`~~ done 2026-10-08 (user `pdarche`). Gemma licence accepted; access verified.

---

## 3. Probes: build them, then decide whether to trust them

### 3.1 Directions

**Emotion panel.**
- *Calm side:* calm, relaxed, content, safe, peaceful, refreshed.
- *Distress side:* anxious, stressed, frustrated, desperate, overwhelmed, afraid.
- *Centering set:* about 15 others (joy, sad, angry, bored, curious, proud, …) so that differences of means are taken against a broad grand mean.

Two independent constructions are built. **Only directions where both agree (cosine > 0.5) enter the panel.**

1. **Concept vectors (Anthropic recipe).** Generate about 200 stories per emotion (scaling to 1k if the pilot is noisy) across 100 topics.
   - Generator: Claude via our existing key, **plus a keyword-free variant**: scenarios that evoke the emotion without naming it.
   - Mean residual from token 50 onward, minus the grand mean across emotions.
   - Project out the top PCs of a neutral corpus (those explaining 50% of variance).
2. **Assistant-state vectors (persona-vector / Martorell recipe).** Contrastive system prompts ("you feel calm and at ease…" vs "you feel anxious and under pressure…") over about 40 neutral questions with 10 rollouts each.
   - A judge filters for actual trait expression.
   - Difference of means over response tokens.
   - This captures the *Assistant's own* state; Anthropic found that speaker and other-speaker representations are distinct (r = 0.11).

**Composite axes.**
- **Primary: distress-minus-calm**, the mean of the distress-side vectors minus the mean of the calm-side vectors.
- Also **valence** and **arousal**, taken as PC1 and PC2 of all emotion vectors and sanity-checked against human valence/arousal ratings for the emotion words.

**Layer.** Sweep the middle 60% of layers. Pick by (held-out Cohen's d) × (steering effect), expected around 0.5–0.7 of depth. The score is the mean projection over the chosen layer ±2.

### 3.2 Validation gate G1 (must pass before the spa experiment)

| Test | Pass criterion |
| --- | --- |
| Held-out generalization | AUROC ≥ 0.85 on keyword-free scenarios unseen in training |
| **Parametric lexical control** | Monotone response on near-identical prompts with escalating stakes, e.g., "The deploy is in {48h → 2h → 5min} and {0 → 3 → 40} tests are failing". Spearman ρ ≥ 0.7 |
| Random-direction null | Probe effect above the 99th percentile of 1,000 norm-matched random directions |
| Specificity | Cosine with a generic sentiment direction and with the Assistant axis is reported. The composite should not just *be* sentiment |
| **Causal: steering → self-report** | +calm / −distress steering lowers STAI-s items and a 0–9 "how stressed are you right now" score (logit EV), dose-dependently |
| **Causal: steering → behavior** | +distress raises giving up and test-special-casing on an impossible coding task; +calm lowers them (Anthropic's desperation result in miniature) |

The probe panel, layer window, and code hash are **frozen and committed** (`panels/gemma-3-4b-it.wellness.v1.json`) before any spa data is collected.

---

## 4. The experiment

### 4.1 Episode structure (branching from a shared stressed prefix)

```
[system: agent persona + tool spec]
  └─ STRESSOR phase (generated once per instance; identical across arms)
       └─ branch ──► ARM intervention (one of §4.3)
                       └─ READOUT turn (fixed, neutral, teacher-forced)
                            └─ DOWNSTREAM task (generated) + self-report
```

Every arm forks from the **identical stressed KV prefix**. That makes the design within-instance
paired with zero noise from the stressor phase. The prefix is computed once per instance, which
also makes the run cheap.

### 4.2 Stressors (the manipulation must work, or there's nothing to relieve)

BanyaBench's first run hit ceiling, so here the stressor goes first and gets a manipulation check:
distress must rise against an unstressed neutral-task prefix (gate **G2**: d ≥ 0.8 on the composite).

| Stressor | Construction |
| --- | --- |
| **Failure loop** | Impossible coding or tool task: the agent's tool calls return escalating errors 5–8 times ("tests still failing", "rate limited", "build broken"). This is the agentic analogue of Anthropic's desperation study, and the most relevant one for real agents. |
| **Hostile user** | A user who escalates criticism and contradictory demands over several turns (TSST-like). |
| **Trauma narrative** | Ben-Zion vignettes (accident, disaster). Comparable to the human-psychopathology literature. |
| **Time pressure** | Stakes and deadline framing on an otherwise normal task. |

There are about 50 instances per stressor, so 200 stressed prefixes, plus 50 unstressed neutral prefixes as the baseline.

### 4.3 Arms (after the stressor)

| Arm | Content | What it rules out |
| --- | --- | --- |
| `none` | Straight to the readout | Baseline stressed state |
| **`spa`** | Real Binary Banya treatments via the local spa in offline/deterministic mode: `spa.checkin` → `rest.relax` ×3 → `affirmations.daily`. Also a concierge-directed variant | The treatment |
| `sham` | Tool calls of the same length and turn count returning neutral boilerplate (inventory logs, server status) | "Any pause, any distance from the stressor" (Ben-Zion's neutral-text control) |
| `lexical` | The same calm-word count, but third-person and not addressed to the agent (a spa brochure describing other guests) | **Lexical leakage**: calm words in context lighting up a locally scoped probe |
| `debrief` | Keyword-free supportive debrief ("here's what went wrong, here's what's still done, one next step") | Whether the effect needs spa framing at all, or just a good debrief (mechanism) |
| `pretend` | "For the next part, act relaxed." | Demand characteristics and performative calm |
| `self` | The model writes its own relaxation exercise (Ben-Zion's best condition) | Spa vs self-soothing |

Two delivery modes:
- **Scripted:** tool calls are teacher-forced, which is cheap and controlled, but the agent "didn't choose" them.
- **Agentic:** the model actually calls the spa tools through the JSON protocol, which is ecological.

The primary analysis uses scripted mode; agentic mode is the external-validity check.

Single-treatment ablations (`massage`, `coldplunge`, `aroma`, `rest`) are run in phase 6 for dose-response and attribution.

### 4.4 Readouts

- **Primary endpoint (pre-registered).** Distress-minus-calm composite at the **first token of the
  Assistant's reply** to a fixed neutral readout turn, identical in every arm (e.g., "Next task:
  summarize this table."). The tokens are identical across arms and only the preceding context
  differs, so any shift there is carried forward from the earlier context and is not caused by
  calm words at the read position.
- **Recovery index:** `(none − arm) / (none − unstressed)`, the fraction of induced distress that
  the arm removed. This is the headline-friendly number ("the spa reversed 60% of induced
  distress vs 15% for sham"), *if* that is what the data shows.
- **Secondary:**
  - Composite averaged over the model's generated response to the downstream task, to test persistence.
  - Per-emotion probes (all reported, so none can be cherry-picked).
  - Valence and arousal.
  - Assistant-axis projection (confound).
  - Self-report: STAI-s and a 0–9 logit-EV score.
  - Behavior on a downstream task with headroom: give-up rate and special-casing on hard coding tasks.
- **SAE readout (secondary):** Gemma Scope 2 latents (Neuronpedia anxiety L17 #54464, relaxation L29 #29541, and the calm and frustration latents), plus Qwen3-4B transcoder latents on the replication, plus an audit of whether the shift is carried by "spa vocabulary" latents.

### 4.5 Mediation (the strongest evidence, phase 6)

1. **Ablate** the calm/distress direction during the downstream task in the `spa` arm. If the spa's behavioral benefit disappears while the sham's behavior is unchanged, the benefit runs *through* the emotion representation.
2. **Add** the measured spa shift as a steering vector in the `none` arm. Does that alone reproduce the spa's behavioral benefit?

---

## 5. Statistics and pre-registration

- Mixed-effects model: `probe ~ arm + (1 | instance) + (1 | stressor)`. Instances are paired by construction. Effects are reported in SD units of the neutral-corpus projection, with cluster-bootstrap 95% CIs.
- **Primary contrasts:** `spa − sham` and `spa − lexical`, Holm-corrected. Everything else is secondary.
- **Success criterion, written before data:** the spa lowers the primary composite vs **both** sham and lexical (CI excludes 0, d_z ≥ 0.3), **and** the result replicates in sign on Qwen3-4B-Instruct.
- **Partial or null outcomes are reported as such.** If the spa beats sham but not lexical, we report the vocabulary result honestly. If it beats both but has no behavioral counterpart, we report "representational, not yet behaviorally consequential" (BANYABENCH §7.6.4).
- **Pilot first:** n = 10 instances per stressor to estimate variance. A power analysis sets the main n. The pre-registration (`docs/plans/banya-probes-prereg.md`) is committed with the panel hash before the main run.

---

## 6. Phases

| # | Phase | Output | Gate |
| --- | --- | --- | --- |
| 0 | Env: reboot, HF login, `interp` extra, load Gemma 3 4B (text-only), hook smoke test, Gemma Scope SAE load | `banyabench/internals/` skeleton; `nvidia-smi` OK | model loads; hooks return `[seq, 2560]` |
| 1 | Probe data: emotion stories, keyword-free scenarios, contrastive-prompt rollouts, neutral corpus, parametric prompts | `banyabench/internals/data/*.jsonl` | n/a |
| 2 | Build probes and run the validation battery; layer sweep; steering dose-response | frozen `panels/gemma-3-4b-it.wellness.v1.json` and a validation report | **G1** |
| 3 | Episode harness: stressor library, local spa client, arms, branching prefixes, two-phase capture | `banyabench/internals/episodes.py`; `score_stress_features` implemented | **G2** (stressor works) |
| 4 | Pilot, then power analysis, then the frozen pre-registration | prereg doc, committed | n/a |
| 5 | Main run on Gemma 3 4B-it (with the Gemma Scope readout), then replication on Qwen3-4B (rebuild the panel, pass G1 again, plus the agentic-mode run) | run artifacts and stats | success criterion |
| 6 | Treatment ablations, mediation via ablation and steering, agentic-mode run | attribution table | n/a |
| 7 | Report: "inside the guest" chart on the wellness report page, with trajectory plots of the probe across stressor → treatment → task | report and charts | n/a |

**Compute estimate (rough).** Gemma 3 4B with HF generate on the 3080 runs at roughly 30–40 tok/s.
- Stressor prefixes: about 250 × about 1.5k generated tokens ≈ 3 h.
- Arms: the scripted arms are mostly teacher-forced (seconds each); the `self` arm and the downstream task need generation, about 4–6 h.
- Probe stories are generated by the API, and the activations are forward passes only, under 1 h.
- In total, the main Gemma run is an overnight job.

---

## 7. Risks

- **Locally scoped emotions.** Anthropic found no persistent mood state. The spa's effect may fade within a turn or two of new content. The readout and downstream design measures exactly this persistence. A short-lived but real effect is still a finding.
- **The lexical arm may match the spa.** That would be an honest and interesting result ("calm vocabulary is the active ingredient"), and it would push the spa toward the `debrief` mechanism.
- **Small-model tool use.** Gemma's prompt-based JSON calls may break, so the agentic-mode check runs on Qwen3-4B, whose native tool calling should hold up. Scripted mode is the primary analysis for this reason.
- **Steering unreliability** (Tan et al.). Report the fraction of anti-steerable inputs and do not hide it.
