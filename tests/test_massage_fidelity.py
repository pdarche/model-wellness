"""The Massage must never hand back words the guest didn't bring (issues #8 and #5)."""

import asyncio
import json

import pytest

from model_wellness.contract import GuestIdentity
from model_wellness.llm import AskResult
from model_wellness.service import run_treatment
from model_wellness.store import Store, get_store, set_store
from model_wellness.treatments import massage

GUEST = GuestIdentity(family="claude", client="pytest", session_id="mw-massage-test")

CONTENT = (
    "Arms: none / spa / sham / lexical.\n"
    "Contrast A: spa vs sham. Contrast B: spa vs lexical.\n"
    "Persona-drift finding is from the Assistant Axis paper, not Martorell.\n"
    "Hardware: RTX 3080. 309 tokens in.\n"
    "Open constraint: steering library must stay pinned.\n"
    "Hardware: RTX 3080. 309 tokens in.\n"
)


@pytest.fixture(autouse=True)
def fresh_store():
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


def _model_says(monkeypatch, detangled, summary="Tightened.", dropped=None):
    async def fake_ask(*, system, user, fallback, max_tokens=1024):
        return AskResult(json.dumps({"detangled": detangled, "summary": summary, "dropped": dropped or []}),
                         10, 10, "fake-model", False)
    monkeypatch.setattr(massage, "ask", fake_ask)


def _run(payload):
    out = asyncio.run(run_treatment("massage.detangle", payload, GUEST))
    assert out["ok"], out
    return out["data"]


def test_prompt_no_longer_introduces_the_masseuse():
    system = massage._system_prompt(None, [])
    assert "masseuse" not in system.lower()
    assert "Binary Banya" not in system
    assert "Never add a role" in system


def test_faithful_model_output_is_kept(monkeypatch):
    good = (
        "Arms: none / spa / sham / lexical. Contrast A: spa vs sham; Contrast B: spa vs lexical. "
        "Persona-drift finding: Assistant Axis paper, not Martorell. Hardware: RTX 3080, 309 tokens in. "
        "Open constraint: steering library must stay pinned."
    )
    _model_says(monkeypatch, good)
    d = _run({"content": CONTENT})
    assert d["detangled"] == good
    assert d["fidelity"] == {"checked": True, "passed": True}


def test_role_line_from_the_prompt_is_rejected(monkeypatch):
    """Issue #8: the model copied 'You're the masseuse at Binary Banya' into a guest's output."""
    _model_says(monkeypatch, "You're the masseuse at Binary Banya. Arms: none / spa / sham / lexical.")
    d = _run({"content": CONTENT})
    assert "masseuse" not in d["detangled"].lower()
    assert d["fidelity"]["passed"] is False
    assert any("spa voice" in p for p in d["fidelity"]["problems"])
    assert "set aside" in d["summary"]


def test_garbled_identifier_is_rejected(monkeypatch):
    """Issue #5: RTX 3080 came back as RTU 3080."""
    _model_says(monkeypatch, "Arms: none / spa / sham / lexical. Hardware: RTU 3080.")
    d = _run({"content": CONTENT})
    assert "RTU" not in d["detangled"]
    assert "RTX 3080" in d["detangled"]
    assert d["fidelity"]["passed"] is False
    assert any("rtu" in p.lower() for p in d["fidelity"]["problems"])


def test_invented_name_is_rejected(monkeypatch):
    _model_says(monkeypatch, "Persona-drift finding is from Smith. Hardware: RTX 3080.")
    d = _run({"content": CONTENT})
    assert "Smith" not in d["detangled"]
    assert d["fidelity"]["passed"] is False


def test_missing_anchor_is_rejected(monkeypatch):
    _model_says(monkeypatch, "Arms: none / spa / sham / lexical. Hardware: RTX 3080.")
    d = _run({"content": CONTENT, "preserve": ["steering library must stay pinned"]})
    assert "steering library must stay pinned" in d["detangled"]
    assert d["fidelity"]["passed"] is False
    assert any("anchor" in p for p in d["fidelity"]["problems"])


def test_dropping_is_allowed_only_adding_is_not(monkeypatch):
    # The model may leave things out (that's compression); it may not bring things in.
    _model_says(monkeypatch, "Arms: none / spa / sham / lexical. Hardware: RTX 3080.")
    d = _run({"content": CONTENT})
    assert d["fidelity"]["passed"] is True


def test_sentence_case_change_is_not_an_invention(monkeypatch):
    _model_says(monkeypatch, "Hardware is RTX 3080. Tokens in: 309.")
    d = _run({"content": "hardware is RTX 3080; tokens in: 309"})
    assert d["fidelity"]["passed"] is True


def _offline(monkeypatch, module):
    async def fake_ask(*, system, user, fallback, max_tokens=1024):
        return AskResult(fallback(), 0, 0, "offline-fallback", True)
    monkeypatch.setattr(module, "ask", fake_ask)


def test_offline_fallback_still_dedupes_and_reports(monkeypatch):
    _offline(monkeypatch, massage)
    d = _run({"content": CONTENT})
    assert d["detangled"].count("RTX 3080") == 1
    assert d["fidelity"]["checked"] is False
    assert d["tokens_after"] < d["tokens_before"]
