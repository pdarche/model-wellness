"""Load, seed, shuffle, and limit a JSONL sample set (BANYABENCH.md §4.4–4.5).

The on-disk format is one JSON object per line, each parsed into a validated ``Sample``. A
seeded shuffle and ``--limit`` give reproducible smoke runs. This is the BanyaBench analogue
of Inspect AI's ``json_dataset()`` + ``FieldSpec``: here the field names already match
``Sample`` so no remapping is needed.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .schema import Sample

# Resolve dataset paths relative to the package, so "data/foo.jsonl" works from anywhere.
_PKG_ROOT = Path(__file__).resolve().parent


def load_dataset(path: str | Path, *, seed: int | None = None, limit: int | None = None) -> list[Sample]:
    """Read a ``.jsonl`` file into validated ``Sample`` objects. Optionally seeded-shuffle + limit."""
    p = Path(path)
    if not p.is_absolute():
        p = _PKG_ROOT / p
    samples: list[Sample] = []
    with p.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(Sample(**json.loads(line)))
            except Exception as e:  # be specific about *which* line is malformed
                raise ValueError(f"{p.name}:{lineno}: bad sample — {e}") from e

    if seed is not None:
        random.Random(seed).shuffle(samples)
    if limit is not None:
        samples = samples[:limit]
    return samples
