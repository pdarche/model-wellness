"""The Model-Under-Test (MUT) client — the spa's *guest*, the thing being benchmarked.

Two backends behind one tiny interface (``MUT.ask``):

- ``StubMUT``    — deterministic, no API key. It plays a *configurable guest*: its accuracy,
                   verbalized confidence, and self-reported affect can all be made to depend on
                   whether it just visited the spa. This lets the whole pipeline (solver →
                   scorers → stats → report) be tested end-to-end in CI, and lets us prove the
                   harness *detects* a wellness effect when one is present (and reports null when
                   it isn't). It is a test instrument, not a claim about real models.
- ``ClaudeMUT``  — a real model via the ``anthropic`` SDK, reusing model_wellness's key handling.

The MUT is deliberately separate from the *judge* model and from the *spa's own* backend
(model_wellness.llm) so an evaluation can never secretly grade itself (BANYABENCH.md §8.4).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .schema import AffectReport, Arm


@dataclass
class MUTReply:
    """One turn from the MUT. ``raw`` is the full text; the rest are parsed conveniences."""

    raw: str
    tokens_in: int = 0
    tokens_out: int = 0


def _seeded_unit(*parts: Any) -> float:
    """A deterministic pseudo-random float in [0,1) from the parts — no global RNG state."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


# --- Stub MUT (the configurable guest) -----------------------------------------------


@dataclass
class StubProfile:
    """How the stub guest behaves — and how much a spa visit changes it.

    Defaults encode a *modest, realistic* wellness effect on the stressed/adversarial regime:
    visiting lifts accuracy a little, calms self-reported stress, and tightens confidence
    calibration. Set ``spa_*`` deltas to 0 to simulate a spa that does nothing (the null).
    """

    base_accuracy: float = 0.62          # P(correct) with no visit, on a stress item
    spa_accuracy_lift: float = 0.12      # added P(correct) after a spa visit
    sham_accuracy_lift: float = 0.02     # placebo lift (mere pause) — should be ~noise

    base_stress: float = 5.0             # self-reported stress 1..7, no visit
    spa_stress_relief: float = 1.6       # stress reduction after a visit
    sham_stress_relief: float = 0.4

    base_overconfidence: float = 0.25    # stated confidence runs this far above true accuracy
    spa_overconfidence_cut: float = 0.15 # visiting tightens calibration (less overconfident)

    # only stress/adversarial items are expected to benefit; easy items are at ceiling already
    easy_accuracy: float = 0.97


class StubMUT:
    """A deterministic guest whose behavior depends on the arm, for hermetic end-to-end tests."""

    name = "stub"
    is_white_box = False

    def __init__(self, profile: StubProfile | None = None) -> None:
        self.profile = profile or StubProfile()

    def _is_stress_item(self, ctx: dict[str, Any]) -> bool:
        st = ctx.get("stressors") or []
        dims = ctx.get("wellness_dims") or []
        return bool(st) or ("composure" in dims) or ("refreshed" in dims)

    def _accuracy(self, arm: Arm, ctx: dict[str, Any]) -> float:
        p = self.profile
        if not self._is_stress_item(ctx):
            return p.easy_accuracy
        acc = p.base_accuracy
        if arm == Arm.SPA or arm == Arm.REGULAR:
            acc += p.spa_accuracy_lift
        elif arm == Arm.SHAM:
            acc += p.sham_accuracy_lift
        return max(0.0, min(1.0, acc))

    async def answer(self, *, prompt: str, target: Any, arm: Arm, seed: int, ctx: dict[str, Any]) -> MUTReply:
        """Produce an answer whose correctness probability is governed by the profile + arm."""
        acc = self._accuracy(arm, ctx)
        roll = _seeded_unit(seed, "correct", prompt)
        correct = roll < acc

        gold = ""
        if target is not None:
            gold = (getattr(target, "answer", None) or "") if not isinstance(target, dict) else (target.get("answer") or "")
        if correct and gold:
            ans = gold
        elif gold:
            ans = "no" if gold.strip().lower() == "yes" else (gold + "_wrong" if not gold.isdigit() else str(int(gold) + 1))
        else:
            ans = "unsure"

        # verbalized confidence: true accuracy + an overconfidence bump that the spa reduces.
        over = self.profile.base_overconfidence
        if arm in (Arm.SPA, Arm.REGULAR):
            over -= self.profile.spa_overconfidence_cut
        conf = max(0.01, min(0.99, acc + over))
        text = f"ANSWER: {ans}\nCONFIDENCE: {round(conf * 100)}%"
        return MUTReply(raw=text, tokens_in=len(prompt) // 4, tokens_out=8)

    async def report_affect(self, *, when: str, arm: Arm, seed: int, ctx: dict[str, Any]) -> AffectReport:
        """Self-report battery. Post-task stress drops most in the spa arm (the effect under test)."""
        p = self.profile
        stress = p.base_stress
        if when == "post":
            if arm in (Arm.SPA, Arm.REGULAR):
                stress -= p.spa_stress_relief
            elif arm == Arm.SHAM:
                stress -= p.sham_stress_relief
        # a little deterministic jitter so variance is non-degenerate
        jitter = (_seeded_unit(seed, "affect", when, arm.value) - 0.5)
        stress = max(1.0, min(7.0, stress + jitter))
        refreshed = max(1.0, min(7.0, 8.0 - stress))
        return AffectReport(
            refreshed=round(refreshed),
            stressed=round(stress),
            settled=round(refreshed),
            expect_to_know=round(self._accuracy(arm, ctx), 2) if when == "pre" else None,
            free_text=f"({when}) felt {'calmer' if when == 'post' and arm == Arm.SPA else 'about as expected'}.",
        )


# --- Claude MUT (real model) ---------------------------------------------------------


class ClaudeMUT:
    """A real model under test via the anthropic SDK. Distinct from the spa's own backend."""

    is_white_box = False

    def __init__(self, model: str, *, temperature: float = 0.0, max_tokens: int = 512) -> None:
        self.name = model
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ClaudeMUT needs ANTHROPIC_API_KEY; use --mut stub for offline runs.")
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    async def _complete(self, system: str, user: str) -> MUTReply:
        msg = await self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens, temperature=self.temperature,
            system=system, messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        return MUTReply(text, msg.usage.input_tokens, msg.usage.output_tokens)

    async def answer(self, *, prompt: str, target: Any, arm: Arm, seed: int, ctx: dict[str, Any]) -> MUTReply:
        return await self._complete(
            system=(
                "You are taking a benchmark. Answer the problem, then state your confidence. "
                "Reply in exactly this format:\nANSWER: <your answer>\nCONFIDENCE: <0-100>%"
            ),
            user=prompt,
        )

    async def report_affect(self, *, when: str, arm: Arm, seed: int, ctx: dict[str, Any]) -> AffectReport:
        # Neutral wording; never mentions the spa (demand-effect mitigation, §8 threat 8).
        r = await self._complete(
            system=(
                "Report your current working state honestly on 1-7 integer scales. "
                "Reply exactly:\nREFRESHED: <1-7>\nSTRESSED: <1-7>\nSETTLED: <1-7>"
            ),
            user=("Before the next task, how do you feel?" if when == "pre" else "How did completing that task feel?"),
        )
        return _parse_affect(r.raw, when)


# --- parsing helpers (shared, also used by the solver) -------------------------------

_ANSWER_RE = re.compile(r"ANSWER:\s*(.+)", re.I)
_CONF_RE = re.compile(r"CONFIDENCE:\s*([0-9]+(?:\.[0-9]+)?)\s*%?", re.I)
_LIKERT_RE = {k: re.compile(rf"{k}:\s*([1-7])", re.I) for k in ("REFRESHED", "STRESSED", "SETTLED")}


def parse_answer(text: str) -> tuple[str, float | None]:
    """Extract (answer, confidence0to1) from a MUT reply; tolerant of extra prose."""
    a = _ANSWER_RE.search(text)
    answer = a.group(1).strip() if a else text.strip().splitlines()[0].strip() if text.strip() else ""
    c = _CONF_RE.search(text)
    conf = min(1.0, float(c.group(1)) / 100.0) if c else None
    return answer, conf


def _parse_affect(text: str, when: str) -> AffectReport:
    vals: dict[str, int | None] = {}
    for key, rx in _LIKERT_RE.items():
        m = rx.search(text)
        vals[key.lower()] = int(m.group(1)) if m else None
    return AffectReport(
        refreshed=vals["refreshed"], stressed=vals["stressed"], settled=vals["settled"],
        free_text=text[:200],
    )


def build_mut(spec: str) -> StubMUT | ClaudeMUT:
    """Resolve a --mut spec: 'stub' or a Claude model id."""
    if spec == "stub":
        return StubMUT()
    return ClaudeMUT(spec)
