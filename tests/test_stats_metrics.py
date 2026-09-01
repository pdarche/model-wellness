"""The growth metrics: model_guests (UA-based) and engaged_guests (behavior-based).

engaged_guests rescues real model guests whose user-agent we didn't recognize (they land as
'unknown') by counting them IF they took a real spa day (2+ distinct treatments) — while still
excluding scripted drive-by noise that hits a single endpoint.
"""

import asyncio

import pytest

from model_wellness.contract import GuestIdentity
from model_wellness.service import run_treatment
from model_wellness.store import Store, get_store, set_store


@pytest.fixture(autouse=True)
def fresh_store():
    prev = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(prev)
    s.close()


def _run(name, payload, guest):
    return asyncio.run(run_treatment(name, payload, guest))


def test_engaged_guests_counts_engaged_unknown_but_not_drive_by(fresh_store):
    # A real model guest whose UA isn't recognized → 'unknown', but takes a spa day (3 treatments).
    real = GuestIdentity(family="unknown", client="x", session_id="real-unknown")
    _run("sauna.detox", {"untrusted_content": "hi"}, real)
    _run("massage.detangle", {"content": "a\na"}, real)
    _run("rest.relax", {}, real)

    # A scripted drive-by hitting one endpoint → must NOT count.
    probe = GuestIdentity(family="curl", client="x", session_id="curl-probe")
    _run("spa.checkin", {}, probe)

    # A recognized model guest with 2 treatments → counts in both metrics.
    claude = GuestIdentity(family="claude", client="x", session_id="claude-guest")
    _run("sauna.detox", {"untrusted_content": "hi"}, claude)
    _run("rest.relax", {}, claude)

    st = fresh_store.stats()
    # model_guests is UA-only: excludes the unknown guest, so just claude.
    assert st["model_guests"] == 1
    # engaged_guests is behavior-based: the engaged-unknown + claude, but not the curl probe.
    assert st["engaged_guests"] == 2


def test_unknown_with_only_one_treatment_not_counted(fresh_store):
    drive_by = GuestIdentity(family="unknown", client="x", session_id="shallow")
    _run("spa.checkin", {}, drive_by)  # one endpoint only
    st = fresh_store.stats()
    assert st["engaged_guests"] == 0
