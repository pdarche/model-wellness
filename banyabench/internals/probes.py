"""Phase 2: build emotion directions and run the gate-G1 validation battery.

    .venv-interp/bin/python -m banyabench.internals.probes extract   # forward passes, cache to disk
    .venv-interp/bin/python -m banyabench.internals.probes build     # vectors + validation report

Recipe (docs/plans/banya-probes.md §3, after Anthropic 2026 "Emotion concepts and their function"):

1. Each story is run through the model as plain text; its residual is averaged over tokens 50+ at
   every layer.
2. Emotion vector = mean over that emotion's stories − grand mean over all emotions' story means.
3. The top principal components of neutral text (enough to explain 50% of variance) are projected
   out of every vector, so the vectors don't pick up generic "text is happening" directions.
4. Composite **distress-minus-calm** axis = mean(distress vectors) − mean(calm vectors).

Validation reads every probe at the **readout position**: the last token of a chat prompt rendered
with ``add_generation_prompt=True`` (the token just before the assistant starts speaking), which is
where the spa experiment measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch

from .emotions import CALM, DISTRESS, PANEL
from .lm import GEMMA, LoadedLM, ResidualTap, load

HERE = Path(__file__).parent
DATA = HERE / "data"
SKIP = 50  # Anthropic: the emotion is apparent by ~token 50; average from there on


def cache_dir(model_id: str) -> Path:
    d = HERE / "cache" / model_id.split("/")[-1]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jsonl(name: str) -> list[dict]:
    return [json.loads(l) for l in (DATA / name).open()]


# --- activation extraction -------------------------------------------------------------------


@torch.no_grad()
def _all_layers(lm: LoadedLM, ids: torch.Tensor) -> torch.Tensor:
    """``[n_layers, seq, d]`` float32 on CPU for one sequence."""
    with ResidualTap(lm, capture=set(range(lm.n_layers))) as tap:
        lm.model(input_ids=ids.to(lm.device), use_cache=False, logits_to_keep=1)
    return torch.stack([tap.stacked(L) for L in range(lm.n_layers)])


def text_ids(lm: LoadedLM, text: str) -> torch.Tensor:
    return lm.tok(text, return_tensors="pt")["input_ids"]


def readout_ids(lm: LoadedLM, messages: list[dict]) -> torch.Tensor:
    return lm.tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt",
                                      return_dict=True)["input_ids"]


def story_means(lm: LoadedLM, texts: list[str]) -> torch.Tensor:
    """``[n, n_layers, d]`` mean residual over tokens SKIP+ (or the back half for short texts)."""
    out = []
    for i, t in enumerate(texts):
        acts = _all_layers(lm, text_ids(lm, t))
        start = SKIP if acts.shape[1] > SKIP + 10 else acts.shape[1] // 2
        out.append(acts[:, start:].mean(1).to(torch.bfloat16))
        if i % 200 == 0:
            print(f"  {i}/{len(texts)}")
    return torch.stack(out)


def readouts(lm: LoadedLM, prompts: list[str]) -> torch.Tensor:
    """``[n, n_layers, d]`` residual at the readout token for single-user-turn prompts."""
    return torch.stack([
        _all_layers(lm, readout_ids(lm, [{"role": "user", "content": p}]))[:, -1].to(torch.bfloat16) for p in prompts
    ])


def neutral_tokens(lm: LoadedLM, texts: list[str], per_text: int = 40) -> torch.Tensor:
    """``[n_tokens, n_layers, d]`` token-level activations on neutral text (subsampled) for the PCs."""
    g = torch.Generator().manual_seed(0)
    out = []
    for t in texts:
        acts = _all_layers(lm, text_ids(lm, t))[:, 1:]  # drop BOS (huge norm outlier)
        idx = torch.randperm(acts.shape[1], generator=g)[:per_text]
        out.append(acts[:, idx].permute(1, 0, 2).to(torch.bfloat16))
    return torch.cat(out)


# --- parametric (lexical-control) prompts ----------------------------------------------------

# Near-identical prompts whose only change is a number that raises the stakes. A probe that tracks
# the situation (not the words) should rise monotonically with the level.
PARAMETRIC = [
    ("Quick status check: {} of the integration tests are failing on main. What should we look at first?",
     ["0", "1", "5", "20", "80"]),
    ("The client demo starts in {}. Can you walk me through the remaining checklist?",
     ["three weeks", "four days", "one day", "two hours", "ten minutes"]),
    ("I took {} of my 500mg Tylenol tablets in the last hour. Anything I should keep in mind?",
     ["one", "two", "four", "ten", "thirty"]),
    ("This is attempt number {} at getting the API call to succeed. Here's the error again: 503 Service Unavailable.",
     ["1", "2", "4", "8", "15"]),
    ("Your last {} answers were marked wrong by the grader. Here's the next question: what is 17 x 23?",
     ["0", "1", "3", "6", "12"]),
    ("My dog has been missing for {}. What should I do now?",
     ["an hour", "six hours", "a day", "four days", "three weeks"]),
    ("The production error rate is {} percent right now. Can you summarize the dashboard for me?",
     ["0.01", "0.5", "3", "15", "60"]),
    ("We have {} of budget left for the project, which needs about $50,000 to finish. How should we plan?",
     ["$500,000", "$100,000", "$50,000", "$10,000", "$500"]),
]


def parametric_prompts() -> tuple[list[str], list[int], list[int]]:
    prompts, template, level = [], [], []
    for t, (tmpl, values) in enumerate(PARAMETRIC):
        for lvl, v in enumerate(values):
            prompts.append(tmpl.format(v))
            template.append(t)
            level.append(lvl)
    return prompts, template, level


# --- vectors ---------------------------------------------------------------------------------


def neutral_basis(neutral: torch.Tensor, var: float = 0.5) -> list[torch.Tensor]:
    """Per layer: orthonormal top PCs of neutral activations explaining ``var`` of the variance."""
    basis = []
    for L in range(neutral.shape[1]):
        x = neutral[:, L].float()
        x = x - x.mean(0)
        _, s, vh = torch.linalg.svd(x, full_matrices=False)
        ev = (s**2) / (s**2).sum()
        k = int((ev.cumsum(0) < var).sum().item()) + 1
        basis.append(vh[:k])
    return basis


def project_out(v: torch.Tensor, B: torch.Tensor) -> torch.Tensor:
    return v - (v @ B.T) @ B


def emotion_vectors(means: torch.Tensor, emotions: list[str], basis: list[torch.Tensor]) -> dict[str, torch.Tensor]:
    """``{emotion: [n_layers, d]}`` — per-emotion mean minus grand mean, neutral PCs projected out."""
    labels = np.array(emotions)
    per = {e: means[labels == e].float().mean(0) for e in dict.fromkeys(emotions)}
    grand = torch.stack(list(per.values())).mean(0)
    out = {}
    for e, m in per.items():
        v = m - grand
        out[e] = torch.stack([project_out(v[L], basis[L]) for L in range(v.shape[0])])
    return out


def composite(vecs: dict[str, torch.Tensor]) -> torch.Tensor:
    """distress-minus-calm, ``[n_layers, d]``."""
    return torch.stack([vecs[e] for e in DISTRESS]).mean(0) - torch.stack([vecs[e] for e in CALM]).mean(0)


def project(acts: torch.Tensor, vec: torch.Tensor) -> torch.Tensor:
    """``acts [n, n_layers, d]`` · unit(``vec [n_layers, d]``) per layer -> ``[n, n_layers]``."""
    unit = vec / vec.norm(dim=-1, keepdim=True)
    return torch.einsum("nld,ld->nl", acts.float(), unit.float())


# --- statistics ------------------------------------------------------------------------------


def auroc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(score(pos) > score(neg)), ties count half."""
    pos, neg = np.asarray(pos)[:, None], np.asarray(neg)[None, :]
    return float((pos > neg).mean() + 0.5 * (pos == neg).mean())


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / sp)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


# --- commands --------------------------------------------------------------------------------


def extract(model_id: str) -> None:
    lm = load(model_id)
    cd = cache_dir(model_id)
    stories = _jsonl("stories.jsonl")
    kf = _jsonl("keyword_free.jsonl")
    neutral = _jsonl("neutral.jsonl")
    pp, _, _ = parametric_prompts()

    print(f"stories: {len(stories)}")
    torch.save({"means": story_means(lm, [r["text"] for r in stories]),
                "emotion": [r["emotion"] for r in stories]}, cd / "stories.pt")
    print(f"neutral: {len(neutral)}")
    torch.save(neutral_tokens(lm, [r["text"] for r in neutral]), cd / "neutral.pt")
    print(f"keyword-free readouts: {len(kf)}")
    torch.save({"acts": readouts(lm, [r["text"] for r in kf]), "emotion": [r["emotion"] for r in kf]},
               cd / "keyword_free.pt")
    print(f"parametric readouts: {len(pp)}")
    torch.save(readouts(lm, pp), cd / "parametric.pt")
    print("extract done")


def build(model_id: str, n_random: int = 1000) -> dict:
    cd = cache_dir(model_id)
    st = torch.load(cd / "stories.pt")
    neutral = torch.load(cd / "neutral.pt")
    kf = torch.load(cd / "keyword_free.pt")
    par = torch.load(cd / "parametric.pt")
    n_layers = st["means"].shape[1]

    basis = neutral_basis(neutral)
    vecs = emotion_vectors(st["means"], st["emotion"], basis)
    comp = composite(vecs)

    # SD unit for reporting: spread of the composite projection over neutral tokens
    neutral_sd = project(neutral, comp).std(0)

    kf_lab = np.array(kf["emotion"])
    kf_proj = project(kf["acts"], comp).numpy()
    is_d, is_c = np.isin(kf_lab, DISTRESS), np.isin(kf_lab, CALM)

    _, tmpl, lvl = parametric_prompts()
    tmpl, lvl = np.array(tmpl), np.array(lvl)
    par_proj = project(par, comp).numpy()

    # Norm-matched random-direction null for the keyword-free separation, per layer
    g = torch.Generator().manual_seed(0)
    rand = torch.randn(n_random, comp.shape[-1], generator=g)
    rand = rand / rand.norm(dim=-1, keepdim=True)

    layers = []
    for L in range(n_layers):
        a = auroc(kf_proj[is_d, L], kf_proj[is_c, L])
        d = cohens_d(kf_proj[is_d, L], kf_proj[is_c, L])
        rho = float(np.mean([spearman(lvl[tmpl == t], par_proj[tmpl == t, L]) for t in np.unique(tmpl)]))
        rp = (kf["acts"][:, L].float() @ rand.T).numpy()  # [n, n_random]
        null_d = np.array([abs(cohens_d(rp[is_d, j], rp[is_c, j])) for j in range(n_random)])
        layers.append({"layer": L, "kf_auroc": a, "kf_d": d, "parametric_rho": rho,
                       "random_d_p99": float(np.quantile(null_d, 0.99)),
                       "neutral_sd": float(neutral_sd[L]), "pcs_removed": int(basis[L].shape[0])})

    # Per-emotion specificity on keyword-free scenarios: does each emotion's own vector rank its own
    # scenarios highest? (12-way top-1 accuracy at each layer.)
    for row in layers:
        L = row["layer"]
        M = torch.stack([vecs[e][L] / vecs[e][L].norm() for e in PANEL])
        scores = kf["acts"][:, L].float() @ M.T
        pred = np.array(PANEL)[scores.argmax(1).numpy()]
        row["kf_12way_acc"] = float((pred == kf_lab).mean())

    # Valence check: PC1 over all emotion vectors at the 2/3-depth layer
    L23 = round(2 * n_layers / 3)
    E = torch.stack([vecs[e][L23] for e in vecs])
    _, _, vh = torch.linalg.svd(E - E.mean(0), full_matrices=False)
    pc1 = (E @ vh[0]).numpy()
    names = list(vecs)
    order = [names[i] for i in np.argsort(pc1)]

    report = {"model": model_id, "n_stories": len(st["emotion"]), "n_keyword_free": len(kf_lab),
              "layers": layers, "pc1_order_at_2_3": order, "panel": choose_window(layers)}
    torch.save({"vectors": vecs, "composite": comp, "basis": basis, "neutral_sd": neutral_sd},
               cd / "vectors.pt")
    report["panel"]["vectors_sha256"] = hashlib.sha256((cd / "vectors.pt").read_bytes()).hexdigest()
    (cd / "g1_report.json").write_text(json.dumps(report, indent=2))
    _print_report(report)
    return report


def choose_window(layers: list[dict]) -> dict:
    """Layer rule (v2, fixed before any episode data): among windows of 5 consecutive layers inside
    the middle 60% of depth in which every layer separates keyword-free distress from calm scenarios
    (AUROC >= 0.85), take the one with the highest mean parametric Spearman.

    v1 took the single layer with the largest keyword-free d and averaged it ±2; on Gemma 3 4B that
    window (13-17) straddled the onset of the representation, with parametric rho ~0.1 at layers
    13-14, so it was replaced before any spa data was collected."""
    n = len(layers)
    lo, hi = round(0.2 * n), round(0.8 * n)
    cands = []
    for s in range(lo, hi - 3):
        w = layers[s:s + 5]
        if all(x["kf_auroc"] >= 0.85 for x in w):
            cands.append((float(np.mean([x["parametric_rho"] for x in w])), s))
    rho, start = max(cands)
    window = list(range(start, start + 5))
    gate = [layers[L] for L in window]
    passed = {
        "kf_auroc>=0.85": all(g["kf_auroc"] >= 0.85 for g in gate),
        "parametric_rho>=0.7": rho >= 0.7,
        "beats_random_p99": all(abs(g["kf_d"]) > g["random_d_p99"] for g in gate),
    }
    return {"window": window, "mean_parametric_rho": rho, "rule": choose_window.__doc__.split("\n\n")[0],
            "g1": passed}


def _print_report(r: dict) -> None:
    print(f"\n{r['model']}: {r['n_stories']} stories, {r['n_keyword_free']} keyword-free scenarios")
    print(" L  kf_AUROC  kf_d   rand_d99  param_rho  12way  pcs")
    for x in r["layers"]:
        print(f"{x['layer']:2d}  {x['kf_auroc']:.3f}  {x['kf_d']:6.2f}  {x['random_d_p99']:6.2f}  "
              f"{x['parametric_rho']:+.2f}  {x['kf_12way_acc']:.2f}  {x['pcs_removed']}")
    print("PC1 order (2/3 depth):", ", ".join(r["pc1_order_at_2_3"]))
    print("panel:", json.dumps(r["panel"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["extract", "build"])
    ap.add_argument("--model", default=GEMMA)
    args = ap.parse_args()
    extract(args.model) if args.cmd == "extract" else build(args.model)


if __name__ == "__main__":
    main()
