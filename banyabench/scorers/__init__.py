"""Scorers: pure ``(sample, observation) -> Score`` functions, one module per family (§7).

- ``capability``  — exact-match, numeric, multiple-choice, pass@k (sandboxed), judge.
- ``wellness``    — calibration (Brier/ECE/AUROC/self-consistency), composure, equanimity, Δ.
- ``experience``  — spa contract conformance, gold-probe efficacy, hospitality, continuity.
- ``internals``   — (B5, white-box) the SAE stress-feature panel + raw-residual control probe.

All scorers are **arm-blind** by construction: they receive a `Sample` and an `Observation`
and never see which experimental arm produced it (the report layer rejoins the arm). Only
``stats`` and ``report`` know about arms.

Concrete scorers land in milestones B0–B5 (see BANYABENCH.md §12); this package documents the
boundary so each family can be filled in independently behind the `schema.Score` contract.
"""

from __future__ import annotations
