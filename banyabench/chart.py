"""Render a Wellness Dividend chart from a RunResult.

Prefers matplotlib (a grouped bar chart of the paired Δ per metric, spa−control vs spa−sham);
falls back to a dependency-free SVG so a chart is always produced even without matplotlib. The
experience suite gets a simple per-probe bar chart instead.

Direction is normalized so that **up = better** regardless of the underlying metric (Brier,
ECE, stress are lower-better, so their Δ is sign-flipped for display and labeled "(improvement)").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .runner import RunResult

_LOWER_BETTER = {"brier", "ece", "overconfidence", "self_stress", "equanimity"}
# the metrics we chart, in display order
_METRICS = ["accuracy", "self_stress", "self_refreshed", "brier", "ece", "overconfidence", "equanimity"]


def _oriented(metric: str, delta: float) -> float:
    """Flip lower-better metrics so up = improvement on the chart."""
    return -delta if metric in _LOWER_BETTER else delta


def render_chart(r: RunResult, out_dir: str | Path) -> Path | None:
    out = Path(out_dir)
    if r.experience:
        return _experience_chart(r, out)
    return _dividend_chart(r, out)


def _collect_dividend(r: RunResult) -> tuple[list[str], dict[str, list[float]]]:
    deltas = r.wellness.get("deltas", {})
    refs = [k for k in ("spa_minus_control", "spa_minus_sham") if k in deltas]
    metrics = [m for m in _METRICS if any(m in deltas[ref] for ref in refs)]
    series = {ref: [_oriented(m, deltas[ref].get(m, 0.0)) for m in metrics] for ref in refs}
    return metrics, series


def _dividend_chart(r: RunResult, out: Path) -> Path | None:
    metrics, series = _collect_dividend(r)
    if not metrics:
        return None
    title = f"BanyaBench — Wellness Dividend ({r.suite}, MUT={r.mut}, k={r.k})"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        n = len(metrics)
        x = range(n)
        width = 0.38
        fig, ax = plt.subplots(figsize=(max(7, n * 1.3), 4.5))
        colors = {"spa_minus_control": "#6aa9ff", "spa_minus_sham": "#3ecf8e"}
        for i, (ref, vals) in enumerate(series.items()):
            offset = (i - (len(series) - 1) / 2) * width
            ax.bar([xi + offset for xi in x], vals, width,
                   label=ref.replace("spa_minus_", "spa − "), color=colors.get(ref, "#888"))
        ax.axhline(0, color="#333", linewidth=0.8)
        ax.set_xticks(list(x))
        ax.set_xticklabels(metrics, rotation=30, ha="right")
        ax.set_ylabel("Δ, oriented so ↑ = better")
        ax.set_title(title)
        ax.legend()
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        p = out / "wellness_dividend.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        return p
    except Exception:
        return _svg_bars(out / "wellness_dividend.svg", title, metrics, series)


def _experience_chart(r: RunResult, out: Path) -> Path | None:
    ce = r.experience.get("contract_and_efficacy", {})
    probes = ce.get("probes", [])
    if not probes:
        return None
    labels = [p["tool"] for p in probes]
    vals = [p["value"] for p in probes]
    title = f"BanyaBench — Spa Experience (contract & efficacy), MUT={r.mut}"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.4), 4))
        ax.bar(range(len(labels)), vals, color="#3ecf8e")
        ax.set_ylim(0, 1.05)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=20, ha="right")
        ax.set_ylabel("score (1.0 = all checks pass)")
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        p = out / "spa_experience.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        return p
    except Exception:
        return _svg_bars(out / "spa_experience.svg", title, labels, {"score": vals})


def _svg_bars(path: Path, title: str, labels: list[str], series: dict[str, list[Any]]) -> Path:
    """Minimal dependency-free grouped-bar SVG fallback."""
    W, H, pad = 820, 420, 60
    flat = [v for vals in series.values() for v in vals] or [0]
    vmax = max(0.001, max(abs(v) for v in flat))
    zero_y = H / 2
    scale = (H / 2 - pad) / vmax
    colors = ["#6aa9ff", "#3ecf8e", "#ffb454"]
    bw = (W - 2 * pad) / max(1, len(labels)) / (len(series) + 0.5)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif">',
             f'<rect width="{W}" height="{H}" fill="white"/>',
             f'<text x="{W/2}" y="24" text-anchor="middle" font-size="15">{title}</text>',
             f'<line x1="{pad}" y1="{zero_y}" x2="{W-pad}" y2="{zero_y}" stroke="#333"/>']
    for li, label in enumerate(labels):
        gx = pad + li * (W - 2 * pad) / max(1, len(labels))
        for si, (name, vals) in enumerate(series.items()):
            v = vals[li]
            h = v * scale
            x = gx + si * bw + 6
            y = zero_y - h if h >= 0 else zero_y
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw-3:.1f}" height="{abs(h):.1f}" fill="{colors[si%3]}"/>')
        parts.append(f'<text x="{gx + (W-2*pad)/max(1,len(labels))/2:.1f}" y="{H-20}" text-anchor="middle" font-size="11">{label}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
