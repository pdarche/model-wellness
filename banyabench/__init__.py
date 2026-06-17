"""BanyaBench — measuring the wellness dividend of a visit to Binary Banya.

A benchmark for the *causal effect of a spa visit on a model under test (MUT)*: not a
sequential task→spa→task script, but a controlled, paired experiment that isolates the
restorative properties a spa is supposed to provide across three measurement families —
Capability, Wellness/affect, and the Spa Experience itself.

See ``BANYABENCH.md`` at the repo root for the full design. This package is the harness; it
deliberately does NOT import into the serving path — the spa never depends on its own
benchmark.
"""

from __future__ import annotations

__version__ = "0.1.0"
