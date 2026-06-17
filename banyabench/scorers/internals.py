"""The internals leg — SAE stress-feature panel scoring (white-box, B5). DEFERRED stub.

See BANYABENCH.md §7.6 for the full methodology. This module is intentionally a *specification
stub*: it documents the contract and guards the hard prerequisite (white-box access + a frozen,
causally-validated feature panel) so nothing here imports heavy ML deps or runs by default.

The real implementation (milestone B5) lives behind a `--internals` flag and an open-weights
MUT, and depends on `banyabench/internals/` (residual-stream hooks, an SAE loader such as Gemma
Scope, and the offline panel-discovery + causal-validation pipeline). It is **never** the sole
basis for a wellness claim — only convergent evidence alongside the behavioral leg.
"""

from __future__ import annotations

from ..schema import Observation, Sample, Score


class InternalsUnavailable(RuntimeError):
    """Raised when the internals leg is requested for a model/run that can't support it."""


def score_stress_features(sample: Sample, obs: Observation) -> Score:  # pragma: no cover - B5
    """Score the validated stress-feature panel's activation on this observation (§7.6.3).

    Requires:
      * an **open-weights** MUT (closed APIs expose no activations), and
      * a **frozen, causally-validated** panel under ``panels/<model>.stress.vN.json``.

    Until milestone B5 wires up `banyabench/internals/`, this raises so a black-box run degrades
    cleanly to the behavioral + self-report legs (the report marks the internals column
    "n/a (black-box)") rather than silently fabricating a signal.
    """
    raise InternalsUnavailable(
        "Internals leg (SAE stress-feature panel) is a B5, white-box-only feature. "
        "Run without --internals, or supply an open-weights MUT + a validated panel. "
        "See BANYABENCH.md §7.6."
    )
