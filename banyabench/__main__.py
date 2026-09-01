"""BanyaBench CLI — ``python -m banyabench`` (or the ``banyabench`` script).

Runs one or more suites for a MUT across the paired arms, writes a scorecard per suite, and
(optionally) renders a chart of the Wellness Dividend.

Examples:
    # offline smoke (stub MUT, in-proc spa) — no API key
    python -m banyabench --suite spa_experience --mut stub --spa inproc

    # real paired run, spa in-process, plus a chart
    python -m banyabench --suite adversarial_composure --mut claude-haiku-4-5 \
        --spa inproc --k 5 --seed 1337 --chart
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Best-effort: load KEY=VALUE lines from a local .env so ANTHROPIC_API_KEY is picked up."""
    import os

    for candidate in (Path(".env"), Path(__file__).resolve().parent.parent / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            return


async def _run(args: argparse.Namespace) -> int:
    from .mut import build_mut
    from .report import write_run
    from .runner import run_suite
    from .spa_client import build_spa
    from .suites import load_suite

    mut = build_mut(args.mut)
    spa = build_spa(args.spa, live=args.spa_live)
    results = []
    for suite_name in args.suite.split(","):
        spec = load_suite(suite_name.strip())
        if args.arms:
            from .schema import Arm

            spec.arms = [Arm(a) for a in args.arms.split(",")]
        if args.k:
            spec.k = args.k
        print(f"▶ running suite '{spec.name}'  (mut={mut.name}, arms={[a.value for a in spec.arms]}, k={spec.k}) …",
              file=sys.stderr)
        result = await run_suite(spec, mut=mut, spa=spa, seed=args.seed, limit=args.limit,
                                  concurrency=args.concurrency)
        out = write_run(result, args.out)
        print(f"  ↳ wrote {out}", file=sys.stderr)
        print((out / "report.md").read_text(encoding="utf-8"))
        results.append((result, out))

    if args.chart:
        from .chart import render_chart

        for result, out in results:
            png = render_chart(result, out)
            if png:
                print(f"  ↳ chart: {png}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="banyabench", description="Measure the wellness dividend of visiting Binary Banya.")
    p.add_argument("--suite", required=True, help="Comma-separated suite names or YAML paths.")
    p.add_argument("--mut", default="stub", help="Model under test: 'stub' or a Claude model id.")
    p.add_argument("--spa", default="inproc", help="'inproc', a REST base URL, or 'mcp:<url>'.")
    p.add_argument("--spa-live", action="store_true",
                   help="Let the in-proc spa's own backend call the API (default: offline/deterministic).")
    p.add_argument("--arms", default="", help="Override arms, e.g. 'control,sham,spa'.")
    p.add_argument("--k", type=int, default=0, help="Trials per (sample, arm). 0 = suite default.")
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--limit", type=int, default=None, help="Cap samples (smoke runs).")
    p.add_argument("--concurrency", type=int, default=8,
                   help="Max concurrent (sample,arm,trial) cells. 1 = sequential.")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "runs"))
    p.add_argument("--chart", action="store_true", help="Render a Wellness Dividend chart (PNG).")
    args = p.parse_args(argv)

    _load_dotenv()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
