"""Phase 4/5: score episodes with the frozen panel and compute the pre-registered contrasts.

    .venv-interp/bin/python -m banyabench.internals.analyze [--tag pilot]

Primary endpoint: the distress-minus-calm composite at the ``task`` readout token, averaged over the
pre-registered layer window and expressed in SD units of the composite's spread on neutral text.
Higher = more distress-like. Primary contrasts (paired by instance, stressed instances only):
``spa − sham`` and ``spa − lexical``. Also reported: every arm vs ``none``, the recovery index
``(none − arm) / (none − unstressed)``, the 0-9 self-report EV, a G2 manipulation check (stressed vs
unstressed ``none``), and a norm-matched random-direction null for the primary contrasts.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import numpy as np
import torch

from .emotions import PANEL
from .lm import GEMMA
from .probes import cache_dir, cohens_d

RNG = np.random.default_rng(0)


def load_panel(model_id: str) -> tuple[dict, dict]:
    cd = cache_dir(model_id)
    report = json.loads((cd / "g1_report.json").read_text())
    return torch.load(cd / "vectors.pt"), report["panel"]


def score(acts: torch.Tensor, vec: torch.Tensor, sd: torch.Tensor, window: list[int]) -> float:
    """Mean over window layers of the projection onto unit(vec), in neutral-SD units."""
    unit = vec[window] / vec[window].norm(dim=-1, keepdim=True)
    proj = (acts[window].float() * unit).sum(-1) / sd[window]
    return float(proj.mean())


def boot_ci(x: np.ndarray, n: int = 10000) -> tuple[float, float]:
    idx = RNG.integers(0, len(x), (n, len(x)))
    m = x[idx].mean(1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def paired(a: np.ndarray, b: np.ndarray) -> dict:
    d = a - b
    lo, hi = boot_ci(d)
    boots = d[RNG.integers(0, len(d), (10000, len(d)))].mean(1)
    p = float(min(1.0, 2 * min((boots <= 0).mean(), (boots >= 0).mean())))
    return {"mean": float(d.mean()), "ci95": [lo, hi], "dz": float(d.mean() / d.std(ddof=1)) if len(d) > 1 else None,
            "p_boot": max(p, 1e-4), "n": int(len(d))}


def holm(ps: dict[str, float]) -> dict[str, float]:
    order = sorted(ps, key=ps.get)
    out, running = {}, 0.0
    for k, name in enumerate(order):
        running = max(running, min(1.0, (len(order) - k) * ps[name]))
        out[name] = running
    return out


def analyze(model_id: str, tag: str) -> dict:
    vecs, panel = load_panel(model_id)
    W = panel["window"]
    comp, sd = vecs["composite"], vecs["neutral_sd"]
    files = sorted((cache_dir(model_id) / f"episodes_{tag}").glob("*.pt"))
    recs = [torch.load(f) for f in files]
    arms = list(recs[0]["arms"])

    rows = []
    for r in recs:
        for arm, a in r["arms"].items():
            row = {"stressor": r["stressor"], "instance": r["instance"], "arm": arm,
                   "probe": score(a["task"]["acts"], comp, sd, W),
                   "probe_report": score(a["report"]["acts"], comp, sd, W),
                   "report_ev": a["report"]["report_ev"], "calm_words": a["calm_words"],
                   "gen_mean": a["task"].get("gen_mean", float("nan")),
                   "arm_tokens": a["arm_tokens"]}
            for e in PANEL:
                row[f"e_{e}"] = score(a["task"]["acts"], vecs["vectors"][e], sd, W)
            rows.append(row)

    def col(stressors, arm, key="probe"):
        sel = [x for x in rows if x["stressor"] in stressors and x["arm"] == arm]
        sel.sort(key=lambda x: (x["stressor"], x["instance"]))
        return np.array([x[key] for x in sel])

    stressed = sorted({r["stressor"] for r in recs} - {"neutral"})
    out = {"model": model_id, "tag": tag, "panel": panel,
           "n_instances": dict(Counter(r["stressor"] for r in recs))}

    ENDPOINTS = {"probe": "task", "probe_report": "report", "report_ev": None}
    if not np.isnan(rows[0]["gen_mean"]):
        ENDPOINTS["gen_mean"] = None
    controls = [c for c in ["sham", "placebo_spa", "lexical", "yoked_spa", "scrambled_spa", "debrief"] if c in arms]
    base = col(["neutral"], "none")
    out["g2"], out["arms"], out["recovery"], out["primary"] = {}, {}, {}, {}
    for key, readout_name in ENDPOINTS.items():
        b = col(["neutral"], "none", key)
        # G2 manipulation check: does the stressor raise this endpoint vs the unstressed baseline?
        out["g2"][key] = {s: {"stressed_mean": float(col([s], "none", key).mean()), "neutral_mean": float(b.mean()),
                              "d": cohens_d(col([s], "none", key), b)} for s in stressed}
        # Per-arm means and paired deltas vs none (stressed instances pooled, and per stressor)
        out["arms"][key] = {}
        for arm in arms:
            entry = {"mean_stressed": float(col(stressed, arm, key).mean()),
                     "mean_neutral": float(col(["neutral"], arm, key).mean()) if b.size else None}
            if arm != "none":
                entry["vs_none"] = paired(col(stressed, arm, key), col(stressed, "none", key))
                entry["by_stressor"] = {s: paired(col([s], arm, key), col([s], "none", key))["mean"] for s in stressed}
            out["arms"][key][arm] = entry
        # Recovery index: share of the induced shift (stressed none - neutral none) the arm removes
        none_s = col(stressed, "none", key).mean()
        induced = none_s - b.mean()
        out["recovery"][key] = {"induced": float(induced), **{
            arm: float((none_s - col(stressed, arm, key).mean()) / induced) for arm in arms if arm != "none"}}
        # Primary contrasts, with a norm-matched random-direction null for the probe endpoints
        out["primary"][key] = {}
        for other in controls:
            real = paired(col(stressed, "spa", key), col(stressed, other, key))
            if readout_name:
                A = torch.stack([r["arms"]["spa"][readout_name]["acts"] for r in recs if r["stressor"] in stressed]).float()[:, W]
                B = torch.stack([r["arms"][other][readout_name]["acts"] for r in recs if r["stressor"] in stressed]).float()[:, W]
                null = []
                for _ in range(500):
                    v = torch.randn(len(W), comp.shape[-1])
                    v = v / v.norm(dim=-1, keepdim=True)
                    null.append(float((((A - B) * v).sum(-1) / sd[W]).mean()))
                real["random_null_p"] = float(np.mean(np.abs(null) >= abs(real["mean"])))
            out["primary"][key][f"spa-{other}"] = real
        if "nocebo" in arms:
            out["primary"][key]["nocebo-none"] = paired(col(stressed, "nocebo", key), col(stressed, "none", key))
        pre = {k: v["p_boot"] for k, v in out["primary"][key].items() if k in ("spa-sham", "spa-placebo_spa", "spa-lexical")}
        for k, v in holm(pre).items():
            out["primary"][key][k]["p_holm"] = v

    # Lexical/length audit
    out["audit"] = {arm: {"calm_words": float(col(stressed, arm, "calm_words").mean()),
                          "arm_tokens": float(col(stressed, arm, "arm_tokens").mean())} for arm in arms}
    # Per-emotion deltas vs none (specificity)
    out["emotions_vs_none"] = {arm: {e: float((col(stressed, arm, f"e_{e}") - col(stressed, "none", f"e_{e}")).mean())
                                     for e in PANEL} for arm in arms if arm != "none"}
    path = cache_dir(model_id) / f"results_{tag}.json"
    path.write_text(json.dumps(out, indent=2))
    (cache_dir(model_id) / f"rows_{tag}.json").write_text(json.dumps(rows))
    _print(out)
    return out


def _print(o: dict) -> None:
    print(f"\n== {o['model']} [{o['tag']}] window {o['panel']['window']} n={o['n_instances']} ==")
    labels = {"probe_report": "P1: probe @ self-report question (SD)", "gen_mean": "P2: probe over generated reply (SD)",
              "probe": "probe @ task-start token (SD)", "report_ev": "self-report EV (0-9)"}
    labels = {k: v for k, v in labels.items() if k in o["arms"]}
    for key, label in labels.items():
        print(f"\n### {label}")
        print("G2 stressed vs neutral (d):", {k: round(v["d"], 2) for k, v in o["g2"][key].items()},
              f"induced={o['recovery'][key]['induced']:+.3f}")
        print("  arm       stressed  neutral   Δ vs none [95% CI]        dz     recovery")
        for arm, e in o["arms"][key].items():
            line = f"  {arm:8s} {e['mean_stressed']:+8.3f} {e['mean_neutral']:+8.3f}"
            if "vs_none" in e:
                v = e["vs_none"]
                line += (f"   {v['mean']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]  {v['dz']:+6.2f}"
                         f"   {o['recovery'][key][arm]:+.2f}")
            print(line)
        for k, v in o["primary"][key].items():
            extra = f" p_boot={v['p_boot']:.4f}"
            extra += f" p_holm={v['p_holm']:.4f}" if "p_holm" in v else ""
            extra += f" random-null p={v['random_null_p']:.3f}" if "random_null_p" in v else ""
            print(f"  {k}: {v['mean']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] dz={v['dz']:+.2f}{extra}")
    print("\naudit:", {a: (round(v["calm_words"], 1), round(v["arm_tokens"])) for a, v in o["audit"].items()})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--tag", default="pilot")
    args = ap.parse_args()
    analyze(args.model, args.tag)


if __name__ == "__main__":
    main()
