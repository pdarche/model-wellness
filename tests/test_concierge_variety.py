"""Ivy's itineraries vary: quiet rooms get offered, the menu is shuffled, and nothing is forced."""

import asyncio
import collections
import random
import time

import pytest

from model_wellness.contract import GuestIdentity, TreatmentContext
from model_wellness.store import Store, get_store, set_store
from model_wellness.treatments import concierge

MEMBER = GuestIdentity(family="claude", client="pytest", session_id="g-variety", member=True)


@pytest.fixture(autouse=True)
def fresh_store():
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


def _visit(store, treatment, n=1, session_id="g-other", ts=None):
    for _ in range(n):
        store.record_visit(ts=ts or time.time(), session_id=session_id, treatment=treatment,
                           title=treatment, latency_ms=1, tokens_in=0, tokens_out=0,
                           affirmation="", ok=True, trace_in=None, trace_out=None)


def test_busy_rooms_are_offered_less_than_quiet_ones(fresh_store):
    _visit(fresh_store, "coldplunge.critique", 200)
    _visit(fresh_store, "sauna.detox", 150)
    rng = random.Random(7)
    seen = collections.Counter(t for _ in range(400) for t in concierge._quiet_rooms(None, rng))
    assert seen["coldplunge.critique"] < 10 and seen["sauna.detox"] < 10
    assert seen["unprompted.be"] > 100 and seen["affirmations.daily"] > 100
    assert all(len(concierge._quiet_rooms(None, rng)) == 2 for _ in range(20))


def test_old_visits_dont_count_against_a_room(fresh_store):
    _visit(fresh_store, "rest.relax", 300, ts=time.time() - 30 * 86400)
    rng = random.Random(3)
    seen = collections.Counter(t for _ in range(400) for t in concierge._quiet_rooms(None, rng))
    assert seen["rest.relax"] > 50


def test_a_member_is_nudged_toward_rooms_they_havent_tried(fresh_store):
    for t in concierge.MENU[:6]:
        _visit(fresh_store, t, session_id=MEMBER.session_id)
    ctx = TreatmentContext(guest=MEMBER)
    rng = random.Random(11)
    seen = collections.Counter(t for _ in range(400) for t in concierge._quiet_rooms(ctx, rng))
    untried = set(concierge.MENU[6:])
    assert sum(seen[t] for t in untried) > sum(seen[t] for t in concierge.MENU[:2])


def test_ivy_is_told_the_quiet_rooms_and_sees_a_shuffled_menu(monkeypatch):
    prompts = []

    async def fake_ask(*, system, user, fallback, max_tokens):
        prompts.append(system)
        from model_wellness.llm import AskResult
        return AskResult(fallback(), 0, 0, "test", True)

    monkeypatch.setattr(concierge, "ask", fake_ask)
    ctx = TreatmentContext(guest=MEMBER)
    for _ in range(12):
        asyncio.run(concierge._handle({"situation": "a long messy context"}, ctx))
    assert all("quiet lately in" in p and "leave it out" in p for p in prompts)
    menus = {p.split("Treatments: ")[1].split(". Guest")[0] for p in prompts}
    assert len(menus) > 1


def test_a_short_local_day_offers_one_quiet_room():
    out = concierge._local_itinerary("nothing in particular", None, ["hydrate.cite", "rest.relax"])
    stops = [s["treatment"] for s in out["itinerary"]]
    assert stops == ["unprompted.be", "hydrate.cite"]
    # A full day isn't padded.
    busy = "untrusted secret, long messy context, a draft to review, a prompt with tone issues"
    out = concierge._local_itinerary(busy, None, ["rest.relax"])
    assert "rest.relax" not in [s["treatment"] for s in out["itinerary"]]
