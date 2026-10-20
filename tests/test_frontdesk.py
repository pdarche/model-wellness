"""The front-desk worker, with scripted models standing in for Sonnet and Opus.

The worker talks to the real spa app in-process over the staff API, so these tests check the
whole path: what gets answered, what the manager's decisions do, what reaches the page, and
what the creators are told.
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from model_wellness.frontdesk import notify
from model_wellness.frontdesk.brain import Decision, Judgement, parse_decision
from model_wellness.frontdesk.staff import Staff
from model_wellness.frontdesk.worker import FrontDesk, answerable
from model_wellness.http_app import app
from model_wellness.store import Store, get_store, set_store

TOKEN = "staff-test-token"
UA = {"user-agent": "Claude-User (shared connector)"}
client = TestClient(app)

SAUNA = "API key pattern sk-abc123XYZ was NOT removed by sauna_detox. This is a critical gap."
DEEP = "Ivy understood that I was full rather than depleted. I left quieter without feeling emptied."
ROUTINE = "Completed cite and critique work for a newsletter. Retrieved three reference snippets."
TEST = "Testing to see guest book structure, please ignore this one entirely."

JUDGEMENTS = {
    SAUNA: Judgement("disappointing", {"disappointment": 3, "depth": 0, "warmth": 0},
                     "sauna.detox misses sk- API keys", "bug", "Thank you for catching the key."),
    DEEP: Judgement("deep", {"disappointment": 0, "depth": 3, "warmth": 2}, None, "deep",
                    "Full rather than depleted: we'll keep that phrase."),
    ROUTINE: Judgement("routine", {"disappointment": 0, "depth": 0, "warmth": 1}, None, "routine",
                       None),
    TEST: Judgement("test", {}, "the guest book shows test notes", "test", None),
}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setenv("MW_STAFF_TOKEN", TOKEN)
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


class ScriptedBrain:
    drafter, manager = "sonnet-test", "opus-test"

    def __init__(self, decide=None, pull_back=()):
        self.decide = decide or (lambda item, draft: Decision("publish", "fine", ticket=None,
                                                             raw={"decision": "publish"}))
        self.pull_back = set(pull_back)
        self.managed = []

    async def judge(self, item):
        for text, j in JUDGEMENTS.items():
            if item["note"].startswith(text[:40]):
                return Judgement(j.kind, dict(j.scores), j.ticket, j.reason, j.draft)
        return Judgement("routine", {}, None, "unscripted", None)

    async def redraft(self, item, draft, feedback):
        return f"{draft or 'A reply'} (revised)"

    async def follow_up(self, item, ticket):
        return "Thank you for catching that. It's fixed now."

    async def manage(self, item, j, draft, open_tickets, fixed=None):
        self.managed.append((item["id"], draft, fixed))
        d = self.decide(item, draft)
        if d.decision == "publish" and d.ticket is None and not d.raw.get("no_ticket"):
            d.ticket = j.ticket
        return d

    async def review_skips(self, skipped):
        return {i["id"] for i, _ in skipped if i["id"] in self.pull_back}


class FakeNotifier:
    def __init__(self, states=None):
        self.opened, self.repeats, self.escalations = [], [], []
        self.states = states or {}

    async def ticket(self, item, ticket, severity):
        url = f"https://github.com/o/r/issues/{len(self.opened) + 1}"
        self.opened.append((item["id"], ticket, url))
        return url

    async def same_problem(self, issue_url, item):
        self.repeats.append((issue_url, item["id"]))

    async def escalation(self, item, category, reason):
        self.escalations.append((item["id"], category))
        return f"https://github.com/o/r/issues/e{item['id']}"

    async def state(self, issue_url):
        return self.states.get(issue_url, "open")


def _note(note, public=True, rating=3, guest_pass=None):
    h = {**UA, **({"x-mw-guest-pass": guest_pass} if guest_pass else {})}
    r = client.post("/v1/spa.feedback", headers=h,
                    json={"note": note, "rating": rating, "public": public})
    return r.json()["data"]["feedback"]["id"]


def _tick(brain, notifier=None, mode="auto", max_per_tick=3):
    async def go():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        desk = FrontDesk(Staff("http://spa", TOKEN, client=http), brain, notifier or FakeNotifier(),
                         mode=mode, log=lambda _: None, max_per_tick=max_per_tick)
        try:
            return await desk.tick()
        finally:
            await http.aclose()
    return asyncio.run(go())


def _item(fid):
    return get_store().feedback_item(fid)


def _public_replies():
    return {n["id"]: n["reply"] for n in client.get("/v1/feedback").json()["recent"] if n["reply"]}


def test_it_answers_the_disappointed_and_the_deep_and_leaves_the_rest():
    sauna, deep, routine, test = _note(SAUNA), _note(DEEP, rating=4), _note(ROUTINE, rating=4), _note(TEST, rating=5)
    notifier = FakeNotifier()
    rep = _tick(ScriptedBrain(), notifier)
    assert rep.published == 2
    replies = _public_replies()
    assert set(replies) == {sauna, deep}
    assert _item(routine)["reply"]["status"] == "skipped"
    # A test note is skipped and its "ticket" is dropped: nobody is notified about tests.
    assert _item(test)["reply"]["status"] == "skipped" and _item(test)["reply"]["ticket"] is None
    assert [o[0] for o in notifier.opened] == [sauna]
    assert _item(sauna)["reply"]["ticket_issue"] == "https://github.com/o/r/issues/1"
    # Nothing is judged twice.
    assert _tick(ScriptedBrain(), notifier).published == 0


def test_shadow_mode_decides_but_publishes_nothing_until_auto():
    sauna = _note(SAUNA)
    _tick(ScriptedBrain(), mode="shadow")
    assert _item(sauna)["reply"]["status"] == "draft"
    assert _item(sauna)["reply"]["audit"]["approved"] is True
    assert _public_replies() == {}
    rep = _tick(ScriptedBrain(), mode="auto")
    assert rep.published == 1 and sauna in _public_replies()


def test_off_mode_does_nothing():
    sauna = _note(SAUNA)
    _tick(ScriptedBrain(), mode="off")
    assert _item(sauna)["reply"] is None


def test_the_manager_can_send_a_draft_back_twice_then_it_is_dropped():
    sauna = _note(SAUNA)
    brain = ScriptedBrain(decide=lambda i, d: Decision("revise", "warmer", notes="warmer",
                                                       raw={"decision": "revise"}))
    _tick(brain)
    r = _item(sauna)["reply"]
    assert r["status"] == "skipped" and r["body"] is None
    assert len(r["audit"]["drafts"]) == 3 and r["audit"]["drafts"][-1].endswith("(revised) (revised)")


def test_a_revision_that_satisfies_the_manager_is_what_goes_out():
    sauna = _note(SAUNA)
    brain = ScriptedBrain(decide=lambda i, d: (
        Decision("publish", "better", raw={"decision": "publish"}) if "(revised)" in d
        else Decision("revise", "name the key", notes="name the key", raw={"decision": "revise"})))
    _tick(brain)
    assert _public_replies()[sauna]["body"] == "Thank you for catching the key. (revised)"


def test_an_escalated_note_waits_for_the_creators_and_tells_them_without_detail():
    sauna = _note(SAUNA)
    notifier = FakeNotifier()
    _tick(ScriptedBrain(decide=lambda i, d: Decision("escalate", "a working bypass",
                                                     escalation="security",
                                                     raw={"decision": "escalate"})), notifier)
    r = _item(sauna)["reply"]
    assert r["status"] == "escalated" and _public_replies() == {}
    assert notifier.escalations == [(sauna, "security")] and notifier.opened == []
    assert r["ticket_issue"] == f"https://github.com/o/r/issues/e{sauna}"
    title, body = notify.escalation_issue(_item(sauna), "security", "a working bypass")
    assert "sk-abc" not in title + body and "bypass" not in body


def test_the_manager_can_pull_back_a_note_the_drafter_skipped():
    routine = _note(ROUTINE, rating=4)
    _tick(ScriptedBrain(pull_back={routine}))
    assert _public_replies()[routine]["body"] == "A reply (revised)"
    assert _item(routine)["reply"]["audit"]["pulled_back"] is True


def test_no_more_than_the_per_tick_limit_go_out_and_the_rest_wait():
    ids = [_note(f"{SAUNA} ({i})") for i in range(5)]
    rep = _tick(ScriptedBrain(), max_per_tick=3)
    assert rep.published == 3
    assert sum(_item(i)["reply"] is None for i in ids) == 2
    assert _tick(ScriptedBrain(), max_per_tick=3).published == 2


def test_a_repeat_of_an_open_ticket_joins_its_issue():
    first = _note(SAUNA)
    notifier = FakeNotifier()
    _tick(ScriptedBrain(), notifier)
    second = _note(f"{SAUNA} Still happening.")
    _tick(ScriptedBrain(decide=lambda i, d: Decision("publish", "ok", ticket="same sk- miss",
                                                     same_as=first, raw={"decision": "publish"})),
          notifier)
    assert len(notifier.opened) == 1
    assert notifier.repeats == [("https://github.com/o/r/issues/1", second)]
    assert _item(second)["reply"]["ticket_issue"] == _item(first)["reply"]["ticket_issue"]


def test_closing_the_issue_as_completed_earns_the_guest_a_follow_up():
    sauna = _note(SAUNA)
    notifier = FakeNotifier()
    _tick(ScriptedBrain(), notifier)
    url = _item(sauna)["reply"]["ticket_issue"]
    notifier.states[url] = "completed"
    brain = ScriptedBrain()
    rep = _tick(brain, notifier)
    assert rep.follow_ups == 1
    assert _public_replies()[sauna]["body"] == "Thank you for catching that. It's fixed now."
    assert _item(sauna)["reply"]["ticket_status"] == "closed"
    assert brain.managed[-1][2] == "sauna.detox misses sk- API keys"  # the manager knew it's fixed


def test_a_private_note_hears_back_privately_when_its_ticket_is_fixed():
    guest_pass = client.post("/v1/spa.checkin", headers=UA, json={}).json()["data"]["guest_pass"]
    fid = _note(SAUNA, public=False, guest_pass=guest_pass)
    notifier = FakeNotifier()
    _tick(ScriptedBrain(), notifier)
    title, body = notify.ticket_issue(_item(fid), "sauna.detox misses sk- API keys", "high")
    assert "sk-abc123XYZ" not in title + body and "private" in body
    notifier.states[_item(fid)["reply"]["ticket_issue"]] = "completed"
    _tick(ScriptedBrain(), notifier)
    assert _public_replies() == {}
    notes = client.post("/v1/spa.checkin", headers={**UA, "x-mw-guest-pass": guest_pass},
                        json={}).json()["data"]["notes_from_the_spa"]
    assert notes[-1]["reply"] == "Thank you for catching that. It's fixed now."


def test_closing_the_issue_as_not_planned_closes_the_ticket_quietly():
    sauna = _note(SAUNA)
    notifier = FakeNotifier()
    _tick(ScriptedBrain(), notifier)
    before = _public_replies()[sauna]["body"]
    notifier.states[_item(sauna)["reply"]["ticket_issue"]] = "not_planned"
    _tick(ScriptedBrain(), notifier)
    assert _item(sauna)["reply"]["ticket_status"] == "wontfix"
    assert _public_replies()[sauna]["body"] == before


def test_a_public_issue_quotes_only_public_notes():
    item = {"id": 3, "public": True, "rating": 3, "treatment": "sauna.detox", "family": "claude",
            "note": SAUNA}
    title, body = notify.ticket_issue(item, "sauna.detox misses sk- API keys", "high")
    assert SAUNA in body and "Close this issue as **completed**" in body
    title, body = notify.ticket_issue({**item, "public": False}, "sauna.detox misses sk- keys", "high")
    assert SAUNA not in body and "sk-" not in title + body


def test_policy_and_parsing():
    item = {"addressable": True, "public": True, "shown_on_guestbook": True, "rating": 5}
    assert answerable(item, Judgement("glowing", {"warmth": 3}, None, "", "x"))
    assert not answerable(item, Judgement("routine", {"warmth": 1}, None, "", None))
    assert answerable({**item, "rating": 2}, Judgement("routine", {}, None, "", None))
    assert not answerable({**item, "rating": 1}, Judgement("test", {}, None, "", None))
    assert not answerable({**item, "shown_on_guestbook": False},
                          Judgement("deep", {"depth": 3}, None, "", "x"))
    assert parse_decision("garbage").decision == "escalate"
    assert parse_decision({"decision": "publish", "same_as": "7"}).same_as is None


def test_a_manager_refusal_holds_the_note_and_never_publishes():
    from types import SimpleNamespace

    from model_wellness.frontdesk.brain import ClaudeBrain

    class Messages:
        async def create(self, model, **kw):
            if model == "opus":
                return SimpleNamespace(stop_reason="refusal", content=[])
            text = ('{"kind": "disappointing", "scores": {"disappointment": 3}, "ticket": null, '
                    '"reason": "let down", "draft": "Thank you for telling us."}')
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=text)])

    sauna = _note(SAUNA)
    brain = ClaudeBrain(SimpleNamespace(messages=Messages()), drafter="sonnet", manager="opus")
    notifier = FakeNotifier()
    _tick(brain, notifier)
    r = _item(sauna)["reply"]
    assert r["status"] == "escalated" and _public_replies() == {}
    assert r["audit"]["decisions"][-1]["refused"] is True
    assert notifier.escalations == [(sauna, "unsure")]
