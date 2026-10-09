"""BanyaProbes — the white-box internals leg (BANYABENCH.md §7.6, plan in docs/plans/banya-probes.md).

Reads (and optionally steers) the residual stream of a small open-weights model while it is
stressed, treated at the spa, and asked a fixed neutral readout question. Heavy ML deps (torch,
transformers, sae-lens) live in a separate venv (``.venv-interp``) so the spa runtime and the
rest of BanyaBench stay lean; nothing here is imported by the serving path.
"""
