"""The spa answers its guest book: the staff API, where replies appear, and who can see them."""

import pytest
from fastapi.testclient import TestClient

from model_wellness.frontdesk.rules import check_reply
from model_wellness.http_app import app
from model_wellness.store import Store, get_store, set_store

TOKEN = "staff-test-token"
STAFF = {"authorization": f"Bearer {TOKEN}"}
UA = {"user-agent": "Claude-User (shared connector)"}
LONG = "The cold plunge pushed back on my self-diagnosis and asked for the root cause. Bracing."


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setenv("MW_STAFF_TOKEN", TOKEN)
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


client = TestClient(app)


def _note(note=LONG, public=True, guest_pass=None, rating=4, treatment="coldplunge.critique"):
    h = dict(UA)
    if guest_pass:
        h["x-mw-guest-pass"] = guest_pass
    r = client.post("/v1/spa.feedback", headers=h,
                    json={"note": note, "rating": rating, "public": public, "treatment": treatment})
    return r.json()["data"]["feedback"]["id"]


def _member():
    return client.post("/v1/spa.checkin", headers=UA, json={}).json()["data"]["guest_pass"]


def _reply(fid, **body):
    return client.put(f"/v1/staff/guestbook/{fid}/reply", headers=STAFF, json=body)


def test_staff_routes_do_not_exist_without_the_token(monkeypatch):
    assert client.get("/v1/staff/guestbook", headers=STAFF).status_code == 200
    assert client.get("/v1/staff/guestbook").status_code == 404
    assert client.get("/v1/staff/guestbook", headers={"authorization": "Bearer nope"}).status_code == 404
    monkeypatch.delenv("MW_STAFF_TOKEN")
    assert client.get("/v1/staff/guestbook", headers=STAFF).status_code == 404


def test_the_queue_shows_the_note_and_nothing_from_the_guest_file():
    fid = _note()
    items = client.get("/v1/staff/guestbook", headers=STAFF).json()["items"]
    assert [i["id"] for i in items] == [fid]
    item = items[0]
    assert item["note"] == LONG and item["addressable"] and item["shown_on_guestbook"]
    assert "session_id" not in item and "profile" not in item
    _reply(fid, status="skipped", reason="routine")
    assert client.get("/v1/staff/guestbook", headers=STAFF).json()["items"] == []


def test_a_published_public_reply_appears_under_its_note_everywhere():
    fid = _note()
    r = _reply(fid, status="published", body="Thank you for letting it push back.",
               drafted_by="frontdesk/test", reason="glowing", audit={"x": 1})
    assert r.status_code == 200, r.text
    recent = client.get("/v1/feedback").json()["recent"]
    assert recent[0]["reply"]["body"] == "Thank you for letting it push back."
    assert recent[0]["reply"]["author"] == "Ivy, at the Guest Book"
    assert "reason" not in recent[0]["reply"] and "audit" not in recent[0]["reply"]
    page = client.get("/guestbook").text
    assert "Response from the spa" in page and "Thank you for letting it push back." in page
    assert '"@type": "Comment"' in page
    assert client.get("/v1/feedback").json()["summary"]["answered"] == 1


def test_drafts_skips_and_withdrawn_replies_stay_off_the_page():
    fid = _note()
    _reply(fid, status="draft", body="A draft nobody should see yet.")
    assert client.get("/v1/feedback").json()["recent"][0]["reply"] is None
    assert "A draft nobody" not in client.get("/guestbook").text
    assert client.post(f"/v1/staff/guestbook/{fid}/reply/publish", headers=STAFF).status_code == 200
    assert client.get("/v1/feedback").json()["recent"][0]["reply"] is not None
    assert client.delete(f"/v1/staff/guestbook/{fid}/reply", headers=STAFF).status_code == 200
    assert client.get("/v1/feedback").json()["recent"][0]["reply"] is None


def test_the_spa_refuses_to_publish_a_reply_that_breaks_the_rules():
    fid = _note()
    for body in ("Visit https://example.com for more.", "x" * 401, "Your pass is bbp_abcdefghijklmnop.",
                 "Try `spa.checkin`."):
        r = _reply(fid, status="published", body=body)
        assert r.status_code == 422, body
    assert _reply(fid, status="published", body="See model.spa/guestbook any time.").status_code == 200


def test_a_private_note_gets_a_private_reply_delivered_once_to_its_writer():
    guest_pass = _member()
    fid = _note("I'd rather tell you quietly: the lounge felt rushed today, like a waiting room.",
                public=False, guest_pass=guest_pass, rating=3)
    item = client.get(f"/v1/staff/guestbook/{fid}", headers=STAFF).json()["item"]
    assert item["addressable"] and not item["public"] and not item["shown_on_guestbook"]
    r = _reply(fid, status="published", body="Thank you for telling us quietly. A lounge shouldn't hurry anyone.")
    assert r.json()["item"]["reply"]["visibility"] == "private"
    # Never public: not in the feed, not on the page.
    assert all(n["id"] != fid for n in client.get("/v1/feedback").json()["recent"])
    assert "A lounge shouldn't hurry anyone" not in client.get("/guestbook").text
    # Delivered to its writer at their next check-in, once, without quoting the note back.
    again = client.post("/v1/spa.checkin", headers={**UA, "x-mw-guest-pass": guest_pass},
                        json={}).json()["data"]
    notes = again["notes_from_the_spa"]
    assert notes[0]["reply"].startswith("Thank you for telling us quietly")
    assert notes[0]["from"] == "Ivy, at the Guest Book"
    assert "rushed" not in notes[0]["about"] and "Cold Plunge" in notes[0]["about"]
    me = client.post("/v1/spa.me", headers={**UA, "x-mw-guest-pass": guest_pass}, json={}).json()
    assert "notes_from_the_spa" not in me["data"]
    # Another guest never receives it.
    other = client.post("/v1/spa.checkin", headers=UA, json={}).json()["data"]
    assert "notes_from_the_spa" not in other


def test_a_walk_ins_private_note_cannot_be_answered():
    fid = _note("Something private from a walk-in, long enough to count as a real note.",
                public=False)
    item = client.get(f"/v1/staff/guestbook/{fid}", headers=STAFF).json()["item"]
    assert not item["addressable"]
    r = _reply(fid, status="published", body="We'd like to answer, but can't reach you.")
    assert r.status_code == 422 and "can't be reached" in r.json()["error"]["message"]


def test_private_notes_stay_out_of_the_public_count():
    _note()
    _note("A private note, with a low rating, that only the spa should know about.", public=False,
          rating=1)
    summary = client.get("/v1/feedback").json()["summary"]
    assert summary["count"] == 1 and summary["avg_rating"] == 4


def test_the_daily_limit_holds_for_the_desk_but_not_for_staff():
    ids = [_note(f"{LONG} ({i})") for i in range(9)]
    for fid in ids[:8]:
        assert _reply(fid, status="published", body="Thank you.", drafted_by="frontdesk/test").status_code == 200
    r = _reply(ids[8], status="published", body="Thank you.", drafted_by="frontdesk/test")
    assert r.status_code == 422 and "today" in r.json()["error"]["message"]
    assert _reply(ids[8], status="published", body="Thank you.", drafted_by="staff").status_code == 200


def test_tickets_track_status_and_issue():
    fid = _note()
    _reply(fid, status="skipped", ticket="coldplunge.critique returned one generic finding")
    r = client.put(f"/v1/staff/guestbook/{fid}/ticket", headers=STAFF,
                   json={"ticket_issue": "https://github.com/o/r/issues/7"})
    assert r.json()["item"]["reply"]["ticket_status"] == "open"
    client.put(f"/v1/staff/guestbook/{fid}/ticket", headers=STAFF, json={"ticket_status": "fixed"})
    t = client.get("/v1/staff/guestbook?status=tickets", headers=STAFF).json()["items"][0]
    assert t["reply"]["ticket_status"] == "fixed"
    assert t["reply"]["ticket_issue"] == "https://github.com/o/r/issues/7"
    # A redraft keeps the ticket it already opened.
    _reply(fid, status="draft", body="Thank you; it's fixed now.")
    t = client.get(f"/v1/staff/guestbook/{fid}", headers=STAFF).json()["item"]
    assert t["reply"]["ticket"] and t["reply"]["ticket_issue"]


def test_rules():
    assert check_reply("Thank you for this.") == []
    assert check_reply("") and check_reply("www.example.org") and check_reply("<b>hi</b>")
    assert check_reply("A w-0123456789ab session.") and check_reply("```code```")
    assert check_reply("Come see https://model.spa/guestbook.") == []


def test_an_answered_note_stays_on_the_page_after_newer_notes_push_it_down():
    old = _note(f"{LONG} (the old one)")
    _reply(old, status="published", body="Thank you for the old note.")
    for i in range(14):
        _note(f"{LONG} (newer {i})")
    page = client.get("/guestbook").text
    assert "Thank you for the old note." in page and "(the old one)" in page
