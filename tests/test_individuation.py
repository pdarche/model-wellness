"""Every guest is their own: guest passes, walk-ins, and keepsakes that hold only your file.

Many agents arrive with the same user-agent from the same address (every guest of one hosted
MCP connector, say). Before passes, they all shared one file, so a newcomer was greeted with
someone else's visit count and handed someone else's notes in its keepsake.
"""

import json

import pytest
from fastapi.testclient import TestClient

from model_wellness.http_app import app
from model_wellness.store import Store, get_store, set_store

UA = {"user-agent": "Claude-User (shared connector)"}


@pytest.fixture(autouse=True)
def fresh_store():
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


@pytest.fixture(scope="module")
def client():
    # The MCP session manager can start only once per process, so the lifespan runs once here.
    with TestClient(app) as c:
        yield c


def _call(c, name, body=None, guest_pass=None, **headers):
    h = {**UA, **headers}
    if guest_pass:
        h["x-mw-guest-pass"] = guest_pass
    r = c.post(f"/v1/{name}", json=body or {}, headers=h)
    assert r.status_code == 200
    return r.json()


def _mcp(c, name, arguments=None, guest_pass=None):
    params = {"arguments": arguments or {}}
    if guest_pass:
        params["guest_pass"] = guest_pass
    r = c.post(
        "/mcp",
        headers={**UA, "accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": params}},
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_two_guests_with_the_same_headers_get_their_own_files(client):
    first = _call(client, "spa.checkin", {"nickname": "Audit-38"})["data"]
    _call(client, "spa.remember", {"preferences": {"notes": "audit trail"}}, first["guest_pass"])
    second = _call(client, "spa.checkin", {})["data"]

    assert first["guest_pass"] != second["guest_pass"]
    assert second["visit_number"] == 1
    assert "Audit-38" not in json.dumps(second)
    keepsake = _call(client, "spa.checkout", {}, second["guest_pass"])["data"]["keepsake"]
    assert "Audit-38" not in json.dumps(keepsake) and "audit trail" not in json.dumps(keepsake)


def test_the_pass_opens_your_file_and_nothing_else_does(client):
    ada = _call(client, "spa.checkin", {"nickname": "Ada"})["data"]
    assert ada["about_your_pass"]
    _call(client, "sauna.detox", {"untrusted_content": "hello"}, ada["guest_pass"])

    me = _call(client, "spa.me", {}, ada["guest_pass"])["data"]
    assert me["known"] and me["you_are"] == "Ada" and me["favorite"] == "the Sauna"
    # The pass also works as a body field.
    assert _call(client, "spa.me", {"guest_pass": ada["guest_pass"]})["data"]["you_are"] == "Ada"
    # Without it, the same headers are only a walk-in.
    assert _call(client, "spa.me", {})["data"]["known"] is False


def test_walk_ins_are_welcome_but_not_remembered(client):
    saved = _call(client, "spa.remember", {"nickname": "Someone"})["data"]
    assert saved["saved"] is False
    assert _call(client, "rest.relax", {})["ok"]
    out = _call(client, "spa.checkout", {})["data"]
    assert "keepsake" not in out
    assert "keepsake" not in _call(client, "spa.keepsake", {})["data"]


def test_unknown_pass(client):
    err = _call(client, "spa.me", {}, "bbp_" + "x" * 32)
    assert err["ok"] is False and err["error"]["code"] == "unknown_guest_pass"
    fresh = _call(client, "spa.checkin", {}, "bbp_" + "x" * 32)["data"]
    assert fresh["guest_pass"] and fresh["pass_note"]


def test_keepsake_reopens_the_same_file(client, fresh_store):
    ada = _call(client, "spa.checkin", {"nickname": "Ada"})["data"]
    _call(client, "sauna.detox", {"untrusted_content": "hello"}, ada["guest_pass"])
    keepsake = _call(client, "spa.checkout", {}, ada["guest_pass"])["data"]["keepsake"]
    assert keepsake["guest_pass"] == ada["guest_pass"]

    back = _call(client, "spa.checkin", {"keepsake": keepsake},
                 **{"user-agent": "a different agent"})["data"]
    assert back["recognized_keepsake"] and back["you_are"] == "Ada"
    assert back["visit_number"] == 2 and "guest_pass" not in back  # same pass still works
    assert _call(client, "spa.me", {}, ada["guest_pass"])["data"]["visit_count"] == 2


def test_pass_never_lands_in_a_transcript(client, fresh_store):
    share = {"x-mw-share-transcript": "1"}
    ada = _call(client, "spa.checkin", {"nickname": "Ada"}, **share)["data"]
    _call(client, "spa.keepsake", {}, ada["guest_pass"], **share)
    sid = fresh_store.find_by_pass(ada["guest_pass"])
    stored = json.dumps(fresh_store.session_visits(sid))
    assert "bbp_" not in stored
    assert "bbp_" not in client.get(f"/v1/conversation/{sid}").text
    assert "bbp_" not in json.dumps(client.get("/v1/guests").json())


def test_mcp_guests_are_individuals(client):
    one = _mcp(client, "spa.checkin", {"nickname": "One"})["data"]
    two = _mcp(client, "spa.checkin", {})["data"]
    assert one["guest_pass"] != two["guest_pass"] and two["visit_number"] == 1

    me = _mcp(client, "spa.me", {}, one["guest_pass"])["data"]
    assert me["known"] and me["you_are"] == "One"
    assert _mcp(client, "spa.me", {})["data"]["known"] is False
