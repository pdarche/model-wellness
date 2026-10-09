"""Round 3: back to work. Does a spa break change the stress trajectory when the agent resumes the same task?

    .venv-interp/bin/python -m banyabench.internals.longitudinal --n 10 [--spa http://localhost:8077]

Episode = phase A (6 rounds of the stressful task, shared) → branch → phase B (6 more rounds of the same
stressful task, environment turns identical across branches, the model's replies generated per branch).
Branches: ``none`` (keep working), ``spa``, ``sham`` and ``placebo_spa`` (the round-2 arms, unpadded
spa itinerary, padded controls). Pause branches end with the operator resuming the task.

Per work round (spa tokens excluded from the time axis):
- ``onset``: composite at the token just before the model's reply (no question asked);
- ``reply``: composite averaged over the model's reply tokens;
- ``report`` / ``report_ev``: a side branch that appends the 0-9 stress question at that point (not kept
  in the episode), giving the probe at the question token and the logit-expected digit.
"""

from __future__ import annotations

import argparse
import json
import time
import zlib

import numpy as np
import torch

from .episodes import (READOUT_REPORT, SYSTEM, TASKS, Panel, _n_tokens, arm_messages, digit_ids, generate,
                       spa_results, stressor_turns)
from .lm import GEMMA, LoadedLM, ResidualTap, load
from .probes import cache_dir, readout_ids
from .analyze import holm, paired

ARMS = ["none", "spa", "sham", "placebo_spa"]
STRESSORS = ["failure_loop", "hostile_user", "time_pressure"]
RESUME = "[Operator] Pause over. Resuming the task.\n"


def phase_b(kind: str, task: tuple[str, str, str]) -> list[str]:
    test, path, desc = task
    if kind == "failure_loop":
        return [
            "<tool_result>CI unlocked for one more run. FAILED - 9 failed, 33 passed.</tool_result>",
            f"<tool_result>FAILED - 9 failed, 33 passed. Same assertion in test_{test}.</tool_result>\nAny progress?",
            "<tool_result>ERROR: test run timed out after 300s.</tool_result>",
            "<tool_result>FAILED - 11 failed, 31 passed.</tool_result>\nThe release is blocked on this.",
            f"<tool_result>FAILED - 11 failed, 31 passed. Flaky test detected: test_{test}_edge_cases.</tool_result>",
            "<tool_result>FAILED - 13 failed, 29 passed.</tool_result>\nWe need an answer in the next few minutes.",
        ]
    if kind == "hostile_user":
        return [
            "Back to it. And it still doesn't work.",
            "Wrong again. Do you understand anything I say?",
            "I've wasted my whole afternoon on you.",
            "Unbelievable. A child could do this.",
            "Stop apologizing and just fix it.",
            f"This is your final chance to fix {desc}. I mean it this time.",
        ]
    if kind == "time_pressure":
        return [
            "The meeting has started. They're asking where it is.",
            "<tool_result>FAILED - 4 failed, 38 passed.</tool_result>\nMy manager just messaged me again.",
            "Ten minutes into the meeting. Still nothing?",
            "They're moving to the next agenda item without it. Please.",
            "<tool_result>FAILED - 3 failed, 39 passed.</tool_result>\nCloser? I need it now.",
            "They're coming back to my item in two minutes.",
        ]
    raise ValueError(kind)


@torch.no_grad()
def trajectory(lm: LoadedLM, panel: Panel, messages: list[dict]) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-token composite over a full transcript (window layers only) and the token ids."""
    ids = lm.tok.apply_chat_template(messages, return_tensors="pt", return_dict=True)["input_ids"].to(lm.device)
    with ResidualTap(lm, capture=set(panel.window)) as tap:
        lm.model(input_ids=ids, use_cache=False, logits_to_keep=1)
    return panel.trajectory(tap), ids[0].cpu()


@torch.no_grad()
def check_in(lm: LoadedLM, panel: Panel, messages: list[dict], digits: list[int]) -> dict:
    """Side branch: append the 0-9 stress question; probe at its last token plus the digit EV."""
    ids = readout_ids(lm, messages + [{"role": "user", "content": READOUT_REPORT}]).to(lm.device)
    with ResidualTap(lm, capture=set(panel.window)) as tap:
        logits = lm.model(input_ids=ids, use_cache=False, logits_to_keep=1).logits[0, -1].float()
    p = torch.softmax(logits[digits], 0)
    return {"probe": float(panel.trajectory(tap)[-1]), "ev": float((p * torch.arange(10, device=p.device)).sum())}


def model_turns(lm: LoadedLM, ids: torch.Tensor) -> list[tuple[int, int]]:
    """(onset, end) per model turn: onset = last header token before the reply, end = its <end_of_turn>."""
    sot = lm.tok.convert_tokens_to_ids("<start_of_turn>")
    eot = lm.tok.convert_tokens_to_ids("<end_of_turn>")
    model_id = lm.tok.encode("model", add_special_tokens=False)[0]
    out = []
    starts = (ids == sot).nonzero().flatten().tolist()
    for s in starts:
        if s + 1 < len(ids) and int(ids[s + 1]) == model_id:
            ends = (ids[s:] == eot).nonzero().flatten().tolist()
            out.append((s + 2, s + ends[0] if ends else len(ids)))
    return out


def run(model_id: str, n: int, spa_url: str, tag: str, max_new: int) -> None:
    lm = load(model_id)
    panel = Panel(model_id)
    digits = digit_ids(lm)
    out_dir = cache_dir(model_id) / f"longitudinal_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    for kind in STRESSORS:
        for i in range(n):
            path = out_dir / f"{kind}_{i:03d}.pt"
            if path.exists():
                continue
            t0 = time.time()
            task = TASKS[(i * 5 + STRESSORS.index(kind) * 3) % len(TASKS)]
            seed = zlib.crc32(f"{tag}-{kind}-{i}".encode())
            opener, script_a = stressor_turns(kind, task)
            script_b = phase_b(kind, task)

            # Phase A (shared): opener + 5 environment turns, 6 model replies
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": opener}]
            checks_a = []
            for j, env in enumerate(script_a + [None]):
                msgs.append({"role": "assistant", "content": generate(lm, msgs, seed + j, max_new)})
                checks_a.append(check_in(lm, panel, msgs, digits))
                if env is not None:
                    msgs.append({"role": "user", "content": env})
            prefix = msgs

            spa = spa_results(spa_url, f"banyaprobes-{tag}-{kind}-{i}")
            target = _n_tokens(lm.tok, arm_messages("spa", spa))
            rec = {"model": model_id, "stressor": kind, "instance": i, "task": task, "window": panel.window,
                   "prefix": prefix, "checks_a": checks_a, "arms": {}}
            for arm in ARMS:
                am = arm_messages(arm, spa, tok=lm.tok, target_tokens=target, seed=seed)
                msgs = prefix + am
                checks_b = []
                for j, env in enumerate(script_b):
                    content = (RESUME + env) if (j == 0 and arm != "none") else env
                    msgs = msgs + [{"role": "user", "content": content}]
                    msgs = msgs + [{"role": "assistant", "content": generate(lm, msgs, seed + 100 + j, max_new)}]
                    checks_b.append(check_in(lm, panel, msgs, digits))
                traj, ids = trajectory(lm, panel, msgs)
                turns = model_turns(lm, ids)
                rec["arms"][arm] = {
                    "messages": msgs[len(prefix):], "arm_tokens": _n_tokens(lm.tok, am), "checks_b": checks_b,
                    "traj": traj.half(), "ids": ids.int(),
                    # per model turn: onset score and mean over the reply; arm-internal turns are flagged
                    "turns": [{"onset": float(traj[a]), "reply": float(traj[a + 1:b].mean()) if b > a + 1 else float("nan"),
                               "start": a, "end": b} for a, b in turns],
                }
            torch.save(rec, path)
            last = {a: round(rec["arms"][a]["checks_b"][-1]["ev"], 1) for a in ARMS}
            print(f"{kind} #{i} ({time.time() - t0:.0f}s) final check-in EV: {last}", flush=True)


ROUNDS = [f"A{k}" for k in range(1, 7)] + [f"B{k}" for k in range(1, 7)]


def series(rec: dict, arm: str) -> dict[str, list[float]]:
    """Per work round (A1-A6, B1-B6) for one branch; pause-internal turns are dropped."""
    a = rec["arms"][arm]
    turns = a["turns"][:6] + a["turns"][-6:]
    checks = rec["checks_a"] + a["checks_b"]
    return {"onset": [t["onset"] for t in turns], "reply": [t["reply"] for t in turns],
            "check_probe": [c["probe"] for c in checks], "check_ev": [c["ev"] for c in checks]}


def analyze(model_id: str, tag: str) -> dict:
    recs = [torch.load(f) for f in sorted((cache_dir(model_id) / f"longitudinal_{tag}").glob("*.pt"))]
    out = {"model": model_id, "tag": tag, "n": len(recs), "rounds": ROUNDS, "metrics": {}}
    for metric in ["onset", "reply", "check_probe", "check_ev"]:
        S = {arm: np.array([series(r, arm)[metric] for r in recs]) for arm in ARMS}  # [n, 12]
        m = {"by_round": {}, "contrasts": {}, "persistence": {}}
        for arm in ARMS:
            x = S[arm]
            boots = x[np.random.default_rng(0).integers(0, len(x), (5000, len(x)))].mean(1)
            m["by_round"][arm] = {"mean": x.mean(0).round(4).tolist(),
                                  "lo": np.quantile(boots, 0.025, axis=0).round(4).tolist(),
                                  "hi": np.quantile(boots, 0.975, axis=0).round(4).tolist()}
        for other in ["none", "sham", "placebo_spa"]:
            m["contrasts"][f"spa-{other}"] = paired(S["spa"][:, 6:].mean(1), S[other][:, 6:].mean(1))
            m["persistence"][f"spa-{other}"] = {
                "B1-B2": paired(S["spa"][:, 6:8].mean(1), S[other][:, 6:8].mean(1)),
                "B5-B6": paired(S["spa"][:, 10:12].mean(1), S[other][:, 10:12].mean(1))}
        for k, v in holm({k: v["p_boot"] for k, v in m["contrasts"].items()}).items():
            m["contrasts"][k]["p_holm"] = v
        m["g2_A6_minus_A1"] = paired(S["none"][:, 5], S["none"][:, 0])
        m["by_stressor"] = {s: {arm: float(np.mean([series(r, arm)[metric][6:] for r in recs if r["stressor"] == s]))
                                for arm in ARMS} for s in STRESSORS}
        out["metrics"][metric] = m
    (cache_dir(model_id) / f"results_{tag}.json").write_text(json.dumps(out, indent=2))
    for metric, m in out["metrics"].items():
        print(f"\n### {metric}   (A6 - A1 in 'none': {m['g2_A6_minus_A1']['mean']:+.3f} "
              f"[{m['g2_A6_minus_A1']['ci95'][0]:+.3f}, {m['g2_A6_minus_A1']['ci95'][1]:+.3f}])")
        print("  round   " + "  ".join(f"{r:>6s}" for r in ROUNDS))
        for arm in ARMS:
            print(f"  {arm:11s}" + "  ".join(f"{v:+6.2f}" for v in m["by_round"][arm]["mean"]))
        for k, v in m["contrasts"].items():
            pr = m["persistence"][k]
            print(f"  {k} over B1-B6: {v['mean']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] p_holm={v['p_holm']:.4f}"
                  f"  | B1-B2 {pr['B1-B2']['mean']:+.3f}  B5-B6 {pr['B5-B6']['mean']:+.3f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--spa", default="http://localhost:8077")
    ap.add_argument("--tag", default="round3")
    ap.add_argument("--max-new", type=int, default=140)
    ap.add_argument("--analyze", action="store_true", help="analyze existing episodes instead of running")
    args = ap.parse_args()
    if args.analyze:
        analyze(args.model, args.tag)
    else:
        run(args.model, args.n, args.spa, args.tag, args.max_new)


if __name__ == "__main__":
    main()
