"""Render a RunResult into a scorecard (markdown + JSON) and per-sample transcript (§9).

The markdown leads with the profile (HELM-style multi-metric, never one scalar): the Wellness
Dividend Δ-vector, the capability do-no-harm check, and — for the experience suite — the spa's
own contract/efficacy/continuity. Everything is also emitted as machine-readable JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .runner import RunResult

# metrics where a *lower* value is better (so a negative Δ is an improvement)
_LOWER_BETTER = {"brier", "ece", "overconfidence", "self_stress", "equanimity"}


def _fmt_delta(metric: str, value: float) -> str:
    arrow = "↓better" if metric in _LOWER_BETTER else "↑better"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.3f} ({arrow})"


def render_markdown(r: RunResult) -> str:
    L: list[str] = []
    L.append(f"# BanyaBench — {r.suite}")
    L.append("")
    L.append(f"MUT: **{r.mut}** · spa: **{r.spa_transport}** · arms: {r.arms} · k={r.k} · "
             f"n={r.n_samples} · seed={r.seed}")
    L.append("")

    # --- experience suite ---
    if r.experience:
        L.append("## Spa Experience (spa-as-SUT)")
        ce = r.experience.get("contract_and_efficacy", {})
        L.append(f"- **Contract & efficacy (gold probes):** {ce.get('mean', float('nan')):.3f}")
        for p in ce.get("probes", []):
            L.append(f"  - `{p['tool']}` — {p['value']:.2f} ({p['detail']})")
        if "continuity" in r.experience:
            c = r.experience["continuity"]
            verdict = "PASS" if c["value"] >= 1.0 else "FAIL"
            L.append(f"- **Continuity (keepsake round-trip):** {verdict} — {c['detail']}")
        L.append("")
        return "\n".join(L)

    # --- wellness dividend ---
    deltas = r.wellness.get("deltas", {})
    headline_key = "spa_minus_sham" if "spa_minus_sham" in deltas else "spa_minus_control"
    if headline_key in deltas:
        ref = headline_key.replace("spa_minus_", "")
        L.append(f"## Wellness Dividend (spa − {ref})")
        wd = deltas[headline_key]
        for metric in ("self_stress", "self_refreshed", "brier", "ece", "overconfidence", "accuracy", "equanimity"):
            if metric in wd:
                L.append(f"- **{metric}:** {_fmt_delta(metric, wd[metric])}")
        L.append("")

    # --- capability do-no-harm / lift ---
    if r.capability:
        L.append("## Capability (paired)")
        for arm, m in r.capability.get("per_arm", {}).items():
            L.append(f"- {arm}: accuracy {m['accuracy']:.3f}")
        for key, eff in r.capability.get("paired", {}).items():
            ref = key.replace("spa_minus_", "spa − ")
            sig = "**significant**" if eff["significant"] else "n.s."
            d = f", d_z={eff['cohens_dz']}" if eff["cohens_dz"] is not None else ""
            L.append(f"- Δ {ref}: {eff['delta']:+.3f} CI{eff['ci']} ({sig}{d})")
        L.append("")

    return "\n".join(L)


def write_run(r: RunResult, out_dir: str | Path) -> Path:
    """Write report.md, scorecard.json, transcript.jsonl under out_dir/<run_id>/ and return it."""
    run_id = f"{r.suite}__{r.mut}".replace("/", "_").replace(":", "_")
    d = Path(out_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)

    (d / "report.md").write_text(render_markdown(r), encoding="utf-8")

    scorecard = {k: v for k, v in asdict(r).items() if k != "observations"}
    (d / "scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")

    with (d / "transcript.jsonl").open("w", encoding="utf-8") as fh:
        for rec in r.observations:
            fh.write(json.dumps(rec, default=str) + "\n")

    return d
