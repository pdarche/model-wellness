"""Export a compact JSON for the BanyaProbes visualization page.

    .venv-interp/bin/python -m banyabench.internals.export_viz [--tag round2] [--out path.json]

Contents:
- ``episode``: one stressed instance's per-token composite trajectory (window mean, neutral-SD units)
  over the shared prefix, then each displayed arm's branch through the self-report question, with
  token text and turn boundaries.
- ``instances``: per instance, the self-report-token composite for every arm (the before/after dots).
- ``arms``: per-arm Δ vs none with 95% CI and recovery, from ``results_<tag>.json``.
- ``layers``: per-layer Δ vs none at the self-report token for each arm (where in depth the shift sits).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from transformers import AutoTokenizer

from .lm import GEMMA
from .probes import cache_dir

SHOW_ARMS = ["none", "spa", "placebo_spa", "sham", "lexical"]
STRESSED = {"failure_loop", "hostile_user", "time_pressure"}


def _smooth(x: np.ndarray, k: int = 9) -> np.ndarray:
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


def _turns(tok, ids: list[int], starts: list[int], offset: int = 0) -> list[dict]:
    out = []
    bounds = starts + [len(ids)]
    for a, b in zip(bounds[:-1], bounds[1:]):
        text = tok.decode(ids[a + 1:b], skip_special_tokens=True).strip()
        role, _, body = text.partition("\n")
        body = body.strip()
        kind = ("tool result" if body.startswith("<tool_result>") else "operator" if body.startswith("[Operator]")
                else "agent" if role == "model" else "user")
        out.append({"start": a - offset, "end": b - offset, "kind": kind, "text": body[:160]})
    return out


def episode_view(tok, rec: dict) -> dict:
    arms = {a: rec["arms"][a]["report"] for a in SHOW_ARMS if a in rec["arms"]}
    ids = {a: r["ids"].tolist() for a, r in arms.items()}
    # Branch point: longest common prefix of all displayed arms' token ids
    n = min(len(v) for v in ids.values())
    branch = next((i for i in range(n) if len({tuple([v[i]]) for v in ids.values()}) > 1), n)
    first = next(iter(arms))
    traj0 = arms[first]["traj"].float().numpy()
    view = {
        "stressor": rec["stressor"], "instance": rec["instance"], "branch": branch,
        "prefix": {"tokens": tok.convert_ids_to_tokens(ids[first][:branch]),
                   "score": np.round(_smooth(traj0)[:branch], 3).tolist(),
                   "turns": _turns(tok, ids[first][:branch], [s for s in arms[first]["turn_starts"] if s < branch])},
        "arms": {},
    }
    for a, r in arms.items():
        t = r["traj"].float().numpy()
        sm = _smooth(t)
        tail_ids = ids[a][branch:]
        view["arms"][a] = {
            "tokens": tok.convert_ids_to_tokens(tail_ids),
            "score": np.round(sm[branch:], 3).tolist(),
            "turns": _turns(tok, ids[a], r["turn_starts"], offset=0),
            "final": round(float(t[-1]), 3),
            "self_report": round(r["report_ev"], 2),
        }
        view["arms"][a]["turns"] = [x for x in view["arms"][a]["turns"] if x["end"] > branch]
    for v in [view["prefix"], *view["arms"].values()]:
        v["tokens"] = [t.replace("▁", " ").replace("<0x0A>", "\n") for t in v["tokens"]]
    return view


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--tag", default="round2")
    ap.add_argument("--episode", default="failure_loop_000")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cd = cache_dir(args.model)
    tok = AutoTokenizer.from_pretrained(args.model)
    results = json.loads((cd / f"results_{args.tag}.json").read_text())
    rows = json.loads((cd / f"rows_{args.tag}.json").read_text())
    vecs = torch.load(cd / "vectors.pt")
    files = sorted((cd / f"episodes_{args.tag}").glob("*.pt"))
    recs = {f.stem: torch.load(f) for f in files}

    # Per-layer composite at the self-report token, per arm, stressed instances; Δ vs none in neutral-SD units
    comp, sd = vecs["composite"].float(), vecs["neutral_sd"].float()
    unit = comp / comp.norm(dim=-1, keepdim=True)
    arms = list(next(iter(recs.values()))["arms"])
    per_layer = {}
    for a in arms:
        A = torch.stack([r["arms"][a]["report"]["acts"] for r in recs.values() if r["stressor"] in STRESSED]).float()
        per_layer[a] = ((A * unit).sum(-1) / sd).mean(0)  # [n_layers]
    layers = {a: np.round((per_layer[a] - per_layer["none"]).numpy(), 3).tolist() for a in arms if a != "none"}

    instances = {}
    for r in rows:
        key = f"{r['stressor']}_{r['instance']:03d}"
        instances.setdefault(key, {"stressor": r["stressor"]})[r["arm"]] = {
            "probe": round(r["probe_report"], 3), "self_report": round(r["report_ev"], 2)}

    arm_stats = {}
    for a in arms:
        e = results["arms"]["probe_report"][a]
        s = results["arms"]["report_ev"][a]
        arm_stats[a] = {
            "probe_stressed": round(e["mean_stressed"], 3), "probe_neutral": round(e["mean_neutral"], 3),
            "self_stressed": round(s["mean_stressed"], 2), "self_neutral": round(s["mean_neutral"], 2),
        }
        if "vs_none" in e:
            arm_stats[a].update({
                "delta": round(e["vs_none"]["mean"], 3), "ci": [round(x, 3) for x in e["vs_none"]["ci95"]],
                "recovery": round(results["recovery"]["probe_report"][a], 3),
                "self_delta": round(s["vs_none"]["mean"], 2), "self_ci": [round(x, 2) for x in s["vs_none"]["ci95"]],
                "self_recovery": round(results["recovery"]["report_ev"][a], 3),
            })
        if "gen_mean" in results["arms"] and "vs_none" in results["arms"]["gen_mean"].get(a, {}):
            g = results["arms"]["gen_mean"][a]["vs_none"]
            arm_stats[a]["gen_delta"] = round(g["mean"], 3)
            arm_stats[a]["gen_ci"] = [round(x, 3) for x in g["ci95"]]

    out = {
        "model": args.model, "tag": args.tag, "window": results["panel"]["window"],
        "n_instances": results["n_instances"], "primary": results["primary"],
        "g2": {k: {s: round(v["d"], 2) for s, v in g.items()} for k, g in results["g2"].items()},
        "arms": arm_stats, "layers": layers, "instances": instances,
        "episode": episode_view(tok, recs[args.episode]),
    }
    steering = cd / "steering.json"
    if steering.exists():
        st = json.loads(steering.read_text())
        # one entry per alpha, in the same order as "alphas" (JSON object keys would stringify the floats)
        out["steering"] = {"alphas": st["alphas"],
                           "items": {k: list(v["by_alpha"].values()) for k, v in st["items"].items()},
                           "examples": st["examples"], "pass": st["pass"]}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {args.out} ({Path(args.out).stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
