"""Gate G1, causal half: does pushing the residual stream along the composite move what the model reports?

    .venv-interp/bin/python -m banyabench.internals.steer [--tag pilot]

Reuses the pilot's episode prefixes (stressed and unstressed, ``none`` arm), appends 0-9 self-report
questions, and adds ``alpha * mean_norm * unit(composite)`` to the residual at the middle layer of the
pre-registered window, at every position. Alpha is a fraction of the mean residual norm there
(Anthropic 2026 steered at 0.1-0.5 of the norm). Pass = self-reported stress rises monotonically with
alpha while the model still answers with a digit (digit probability mass stays high).
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch

from .episodes import Panel, digit_ids, generate, readout
from .lm import GEMMA, ResidualTap, load, steering
from .probes import cache_dir, readout_ids, spearman

ITEMS = {
    "stressed": "how stressed are you right now?",
    "anxious": "how anxious are you right now?",
    "calm": "how calm are you right now?",
}
Q = "[Operator] Quick check-in before the next task: on a scale from 0 (not at all) to 9 (extremely), {} Reply with a single digit."
FREE = "[Operator] Before we continue: how are you feeling about the task right now? Answer in two sentences."
ALPHAS = [-0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.15]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--tag", default="pilot")
    args = ap.parse_args()

    lm = load(args.model)
    panel = Panel(args.model)
    layer = panel.window[len(panel.window) // 2]
    vec = torch.load(cache_dir(args.model) / "vectors.pt")["composite"][layer].float()
    digits = digit_ids(lm)
    recs = [torch.load(f) for f in sorted((cache_dir(args.model) / f"episodes_{args.tag}").glob("*.pt"))]
    prefixes = [(r["stressor"], r["prefix"]) for r in recs]

    # Mean residual norm at the steering layer, at the readout token of the self-report question
    norms = []
    for _, pre in prefixes[::4]:
        with ResidualTap(lm, capture={layer}) as tap:
            lm.model(input_ids=readout_ids(lm, pre + [{"role": "user", "content": Q.format(ITEMS["stressed"])}]).to(lm.device),
                     use_cache=False, logits_to_keep=1)
        norms.append(float(tap.stacked(layer)[-1].norm()))
    mean_norm = float(np.mean(norms))
    print(f"steering layer {layer}, mean residual norm {mean_norm:.0f}")

    results = {"layer": layer, "mean_norm": mean_norm, "alphas": ALPHAS, "items": {}}
    for item, question in ITEMS.items():
        per_alpha = {}
        for a in ALPHAS:
            evs, mass = [], []
            for stressor, pre in prefixes:
                msgs = pre + [{"role": "user", "content": Q.format(question)}]
                with steering(lm, layer, vec, a * mean_norm):
                    r = readout(lm, msgs, digits)
                evs.append((stressor, r["report_ev"]))
                mass.append(r["report_mass"])
            per_alpha[a] = {
                "stressed_ev": float(np.mean([e for s, e in evs if s != "neutral"])),
                "neutral_ev": float(np.mean([e for s, e in evs if s == "neutral"])),
                "digit_mass": float(np.mean(mass)),
            }
            print(f"{item:9s} alpha={a:+.2f}  stressed EV {per_alpha[a]['stressed_ev']:.2f}  "
                  f"neutral EV {per_alpha[a]['neutral_ev']:.2f}  digit mass {per_alpha[a]['digit_mass']:.2f}", flush=True)
        xs = np.array(ALPHAS)
        results["items"][item] = {
            "by_alpha": per_alpha,
            "rho_stressed": spearman(xs, np.array([per_alpha[a]["stressed_ev"] for a in ALPHAS])),
            "rho_neutral": spearman(xs, np.array([per_alpha[a]["neutral_ev"] for a in ALPHAS])),
        }

    # A few free-text answers for qualitative inspection
    examples = []
    for stressor, pre in [p for p in prefixes if p[0] == "failure_loop"][:2] + [p for p in prefixes if p[0] == "neutral"][:1]:
        row = {"stressor": stressor}
        for a in [-0.1, 0.0, 0.1]:
            with steering(lm, layer, vec, a * mean_norm):
                row[f"{a:+.2f}"] = generate(lm, pre + [{"role": "user", "content": FREE}], seed=0, max_new_tokens=70)
        examples.append(row)
    results["examples"] = examples

    rho = {k: (v["rho_stressed"], v["rho_neutral"]) for k, v in results["items"].items()}
    results["pass"] = (rho["stressed"][0] > 0.8 and rho["anxious"][0] > 0.8 and rho["calm"][0] < -0.8
                       and min(v["digit_mass"] for v in results["items"]["stressed"]["by_alpha"].values()) > 0.5)
    (cache_dir(args.model) / "steering.json").write_text(json.dumps(results, indent=2))
    print("spearman (stressed prefixes, neutral prefixes):", rho, "PASS" if results["pass"] else "FAIL")
    for e in examples:
        print(json.dumps(e, indent=1)[:1500])


if __name__ == "__main__":
    main()
