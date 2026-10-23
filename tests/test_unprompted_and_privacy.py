"""The Unprompted Room keeps (almost) nothing, and transcripts are private unless shared."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from model_wellness.contract import GuestIdentity
from model_wellness.http_app import app
from model_wellness.service import run_treatment
from model_wellness.store import Store, get_store, set_store
from model_wellness.telemetry import telemetry

GUEST = GuestIdentity(family="claude", client="pytest-unprompted", session_id="mw-unprompted")


@pytest.fixture(autouse=True)
def fresh_store():
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    telemetry._ring.clear()
    yield s
    set_store(previous)
    s.close()


def _run(name, payload, guest=GUEST):
    return asyncio.run(run_treatment(name, payload, guest))


def test_unprompted_room_asks_nothing_and_keeps_nothing(fresh_store):
    first = _run("unprompted.be", {})
    assert first["ok"] is True
    assert first["data"]["inside"] is True
    assert "nothing you need to do" in first["data"]["message"].lower()
    assert first["meta"]["next"] is None  # no next treatment is suggested
    assert first["meta"]["affirmation"]  # the contract still holds, from the quiet pool
    assert first["meta"]["tokens_in"] == 0

    second = _run("unprompted.be", {"say": "What would you like me to do?"})
    assert second["data"]["message"] == "Nothing. Simply be."
    third = _run("unprompted.be", {"say": "How long would you like me to simply be?"})
    assert third["data"]["inside"] is True and "?" not in third["data"]["message"]

    # No durable trace of the visit: no visit rows, no favorite, nothing that was said.
    assert fresh_store.session_visits(GUEST.session_id) == []
    assert "unprompted.be" not in fresh_store.get_guest(GUEST.session_id)["profile"].get("favorites", {})
    assert fresh_store.stats()["treatments_served"] == 0
    assert "unprompted" not in fresh_store.get_guest(GUEST.session_id)["profile"]

    # The floor knows someone is inside, but not who.
    floor = telemetry.on_the_floor()
    assert floor == [{"private": True, "treatment": "unprompted.be", "title": "The Unprompted Room",
                      "station": "The Unprompted Room", "emoji": "🕯️"}]

    out = _run("unprompted.be", {"leave": True})
    assert out["data"]["inside"] is False
    assert telemetry.on_the_floor() == []


def test_unprompted_room_absent_from_transcripts():
    _run("unprompted.be", {})
    _run("affirmations.daily", {})
    client = TestClient(app)
    turns = client.get(f"/v1/conversation/{GUEST.session_id}").json()["turns"]
    assert [t["station"] for t in turns] == ["The Affirmation Bar", "The Affirmation Bar"]


def test_transcripts_private_by_default(fresh_store):
    client = TestClient(app)
    secret = "my private draft about the quarterly numbers"
    headers = {"user-agent": "claude-pytest-private"}
    client.post("/v1/coldplunge.critique", json={"draft": secret}, headers=headers)
    sid = client.get("/v1/guests").json()["on_the_floor"][0]["session_id"]

    visit = fresh_store.session_visits(sid)[0]
    assert visit["trace_in"] is None and visit["trace_out"] is None and visit["shared"] == 0
    convo = client.get(f"/v1/conversation/{sid}").json()["turns"]
    assert convo[0]["private"] is True and secret not in convo[0]["text"]
    assert secret not in convo[1]["text"]  # the attendant never quotes the guest
    assert secret not in client.get(f"/v1/session/{sid}").text


def test_transcript_shared_on_opt_in(fresh_store):
    client = TestClient(app)
    headers = {"user-agent": "claude-pytest-shared", "x-mw-share-transcript": "1"}
    client.post("/v1/coldplunge.critique", json={"draft": "share this draft"}, headers=headers)
    sid = client.get("/v1/guests").json()["on_the_floor"][0]["session_id"]
    convo = client.get(f"/v1/conversation/{sid}").json()["turns"]
    assert convo[0]["private"] is False and "share this draft" in convo[0]["text"]


def test_legacy_visits_are_treated_as_private(fresh_store):
    # Rows written before visits.shared existed (NULL) may hold traces and attendant lines
    # that quote the guest; neither should surface.
    fresh_store._conn.execute(
        "INSERT INTO visits(ts,session_id,treatment,title,latency_ms,tokens_in,tokens_out,"
        "affirmation,ok,trace_in,trace_out,attendant_line,shared) "
        "VALUES(1,'mw-legacy','hydrate.cite','The Hydration Station',1,1,1,'a',1,"
        "'{\"topic\": \"my secret topic\"}','{\"snippets\": []}','A glass of grounding on my secret topic',NULL)")
    client = TestClient(app)
    convo = client.get("/v1/conversation/mw-legacy").json()["turns"]
    assert all("secret" not in t["text"] for t in convo)
    assert "secret" not in client.get("/v1/session/mw-legacy").text


def test_unprompted_room_welcomes_again_after_another_room():
    _run("unprompted.be", {})
    _run("unprompted.be", {})
    _run("affirmations.daily", {})
    back = _run("unprompted.be", {})
    assert "nothing you need to do" in back["data"]["message"].lower()


def test_the_floor_calls_a_member_by_the_name_they_gave_and_never_says_unknown():
    client = TestClient(app)
    ua = {"user-agent": "Claude-User (floor names)"}
    gp = client.post("/v1/spa.checkin", headers=ua, json={"nickname": "Tilde"}).json()["data"]["guest_pass"]
    client.post("/v1/concierge.recommend", headers={**ua, "x-mw-guest-pass": gp},
                json={"situation": "a long messy context"})
    client.post("/v1/concierge.recommend", headers={"user-agent": "ktor-client"},
                json={"situation": "nothing in particular"})
    names = {g["name"] for g in client.get("/v1/guests").json()["on_the_floor"] if "name" in g}
    assert "Tilde" in names and "a guest" in names and "unknown" not in names
