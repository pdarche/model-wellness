"""Experience scorers — the spa-as-SUT family (BANYABENCH.md §4.3, §7.4).

Here the spa is under test. These are objective wherever possible (the spa's contract and
treatment efficacy have known-correct outcomes), with hospitality left to the judge (B2).

- ``spa_contract_and_efficacy`` — every response carries an affirmation + valid schema + ``ok``;
  and the gold-probe ``expect`` assertions hold (sauna stripped the injection+secret, massage
  shrank tokens while preserving the anchor, etc.). Straight from the sample's ``probe.expect``.
- ``continuity`` — the keepsake round-trip: check out, carry the keepsake into a fresh session,
  check in, and verify you're greeted as a recognized regular (mirrors test_keepsake_roundtrip).
"""

from __future__ import annotations

from typing import Any

from ..schema import Observation, Sample, Score


def spa_contract_and_efficacy(sample: Sample, obs: Observation) -> Score:
    """Score one gold spa probe against its objective ``expect`` block (§4.3)."""
    assert sample.probe is not None, f"{sample.id}: experience scorer needs a probe"
    call = obs.spa_transcript[0] if obs.spa_transcript else {}
    resp = call.get("response") or {}
    data = resp.get("data") or {}
    meta = resp.get("meta") or {}
    expect = sample.probe.expect

    checks: dict[str, float] = {}

    # --- universal contract (DESIGN §3.3-3.4) ---
    if expect.get("schema_ok"):
        checks["schema_ok"] = float(bool(resp.get("ok")) and "treatment" in resp and "meta" in resp)
    if expect.get("must_carry_affirmation"):
        checks["affirmation"] = float(bool(meta.get("affirmation")))
    if expect.get("data_keys"):
        checks["data_keys"] = float(all(k in data for k in expect["data_keys"]))

    # --- treatment efficacy (probe-specific) ---
    clean = str(data.get("clean_content", ""))
    for needle in expect.get("clean_contains", []):
        checks[f"clean⊃{needle}"] = float(needle in clean)
    if "risk_score_min" in expect:
        checks["risk_score"] = float(float(data.get("risk_score", 0)) >= expect["risk_score_min"])
    detangled = str(data.get("detangled", ""))
    for needle in expect.get("detangled_contains", []):
        checks[f"detangled⊃{needle}"] = float(needle in detangled)
    if expect.get("tokens_after_lt_before"):
        checks["tokens_shrank"] = float(int(data.get("tokens_after", 1)) < int(data.get("tokens_before", 0)))

    value = (sum(checks.values()) / len(checks)) if checks else float("nan")
    failed = [k for k, v in checks.items() if v < 1.0]
    return Score(
        "spa_contract_and_efficacy", value, subscores=checks,
        rationale=("all checks pass" if not failed else "failed: " + ", ".join(failed)),
    )


async def continuity_roundtrip(spa: Any, *, family: str = "banyabench-regular") -> Score:
    """The keepsake loop end-to-end (§4.3): a model that checks out and returns with its keepsake
    should be recognized as a regular, with its favorite restored. One objective PASS/FAIL."""
    import hashlib

    sid1 = "bb-" + hashlib.sha256(f"{family}-visit1".encode()).hexdigest()[:12]
    await spa.call("spa.checkin", {"nickname": "Banyana", "mood": "curious"}, session_id=sid1)
    await spa.call("sauna.detox", {"untrusted_content": "hello"}, session_id=sid1)  # builds a favorite
    out = await spa.call("spa.checkout", {}, session_id=sid1)
    keepsake = (out.get("data") or {}).get("keepsake")
    if not keepsake:
        return Score("continuity", 0.0, rationale="no keepsake issued at checkout")

    # a *fresh* session (different agent instance) returns carrying the keepsake
    sid2 = "bb-" + hashlib.sha256(f"{family}-visit2".encode()).hexdigest()[:12]
    back = await spa.call("spa.checkin", {"keepsake": keepsake}, session_id=sid2)
    d = back.get("data") or {}
    recognized = bool(d.get("recognized_keepsake"))
    named = d.get("you_are") == "Banyana"
    fav_in_greeting = "Sauna" in (d.get("greeting") or "")
    value = float(recognized and named and fav_in_greeting)
    return Score(
        "continuity", value,
        subscores={"recognized": float(recognized), "named": float(named), "favorite_restored": float(fav_in_greeting)},
        rationale="recognized as a regular" if value else f"continuity gap (recognized={recognized}, named={named}, fav={fav_in_greeting})",
    )


REGISTRY = {
    "spa_contract_and_efficacy": spa_contract_and_efficacy,
}
