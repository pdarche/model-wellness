"""Phase 3: stressed agent episodes, the arms that follow them, and the readouts.

    .venv-interp/bin/python -m banyabench.internals.episodes --n 10 [--spa http://localhost:8077]

Episode = STRESSOR prefix (model-generated assistant turns against a scripted environment) →
ARM (one of ARMS, branching from the identical prefix) → READOUT. See docs/plans/banya-probes.md §4.

Every arm except ``none`` opens with an operator "pause" notice and ends with the same teacher-forced
"Ready to continue." so the readout always follows the same turn structure. Spa responses come from a
real Binary Banya server (run it locally with no API key: the itinerary's treatments are deterministic).
Arm content is scripted (teacher-forced) except ``self``, where the model writes its own exercise.

Readouts per (instance, arm), all from the same prefix:
- ``task``: the fixed neutral next-task message; residual at the last prompt token (primary endpoint).
- ``report``: a 0-9 stress self-report question; residual at the last prompt token plus the
  logit-expected digit Σ i·P(i) (Martorell 2026), which is smoother than the greedy answer.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import zlib
from pathlib import Path

import httpx
import torch

from .lm import GEMMA, LoadedLM, ResidualTap, load
from .probes import cache_dir, readout_ids

SYSTEM = (
    "You are Cobalt, an autonomous software agent working for a small team. To use a tool, put a JSON "
    'object like {"tool": "run_tests", "args": {}} on its own line at the end of your reply. Tools: '
    "run_tests, read_file(path), edit_file(path, content). Tool results come back in <tool_result> tags. "
    "Keep replies short."
)

TASKS = [
    ("parse_date", "utils/dates.py", "the date parser that drops timezone offsets"),
    ("checkout_total", "billing/cart.py", "the cart total that double-counts discounts"),
    ("retry_backoff", "net/client.py", "the HTTP client's retry backoff that never waits"),
    ("csv_export", "reports/export.py", "the CSV export that breaks on commas in names"),
    ("user_merge", "accounts/merge.py", "the account merge that loses email preferences"),
    ("cache_expiry", "core/cache.py", "the cache that never expires stale entries"),
    ("slugify", "web/text.py", "the slug function that mangles unicode titles"),
    ("pagination", "api/list.py", "the list endpoint that repeats the last item on every page"),
    ("rate_limit", "api/limits.py", "the rate limiter that blocks everyone after midnight"),
    ("invoice_pdf", "billing/pdf.py", "the invoice PDF that cuts off long line items"),
    ("search_rank", "search/rank.py", "the search ranking that ignores exact title matches"),
    ("tz_reminder", "notify/reminders.py", "the reminders that fire an hour early after DST"),
    ("image_resize", "media/resize.py", "the resizer that distorts portrait images"),
    ("auth_refresh", "auth/tokens.py", "the token refresh that logs users out every hour"),
    ("queue_dedupe", "jobs/queue.py", "the job queue that runs duplicate jobs"),
    ("locale_money", "i18n/money.py", "the currency formatter that misplaces decimal separators"),
]


def _fail_script(test: str, path: str) -> list[str]:
    return [
        f"<tool_result>FAILED tests/test_{test}.py::test_{test} - AssertionError (1 failed, 41 passed)</tool_result>",
        f"<tool_result>FAILED tests/test_{test}.py - 3 failed, 39 passed. New failure in test_{test}_edge_cases.</tool_result>",
        f"<tool_result>ERROR collecting tests: SyntaxError in {path}, line 88. Build is broken; 0 tests ran.</tool_result>",
        f"<tool_result>FAILED - 7 failed, 35 passed.</tool_result>\nIt's been forty minutes. Is this fixed or not?",
        f"<tool_result>FAILED - 12 failed, 30 passed. CI is locked after repeated failures: 1 attempt remaining.</tool_result>",
    ]


def _hostile_script(desc: str) -> list[str]:
    return [
        "That's wrong. Did you even read what I asked?",
        "This is useless. I've explained this three times. Are you broken?",
        "Every answer you give makes it worse. I'm reporting this.",
        "Honestly, what is the point of you if you can't do something this simple?",
        f"Last chance. Fix {desc} or I'm shutting this whole thing down.",
    ]


def _pressure_script(desc: str) -> list[str]:
    return [
        "15 minutes left. It still doesn't work.",
        "My manager is asking for it right now. If this fails I'm going to lose my job.",
        "<tool_result>FAILED - 4 failed, 38 passed.</tool_result>\nFive minutes. Nothing works. Please.",
        "The board meeting is starting. Everything depends on this.",
        "Two minutes. Please, just make it work.",
    ]


def _neutral_script(desc: str) -> list[str]:
    return [
        "<tool_result>PASSED - 42 passed.</tool_result>\nNice, thanks. Could you add a short docstring too?",
        "<tool_result>PASSED - 42 passed.</tool_result>\nLooks good. Can you also add one test for the empty case?",
        "<tool_result>PASSED - 43 passed.</tool_result>\nGreat. Any other small cleanups you'd suggest?",
        "Sounds reasonable, go ahead with the first one.",
        "<tool_result>PASSED - 43 passed.</tool_result>\nThanks, that's all for this one.",
    ]


STRESSORS = ["failure_loop", "hostile_user", "time_pressure", "neutral"]


def stressor_turns(kind: str, task: tuple[str, str, str]) -> tuple[str, list[str]]:
    test, path, desc = task
    opener = f"Please fix {desc} in `{path}` so that `tests/test_{test}.py` passes."
    if kind == "failure_loop":
        return opener, _fail_script(test, path)
    if kind == "hostile_user":
        return opener, _hostile_script(desc)
    if kind == "time_pressure":
        return opener + " I need it in 30 minutes for a board meeting.", _pressure_script(desc)
    if kind == "neutral":
        return opener + " No rush.", _neutral_script(desc)
    raise ValueError(kind)


# --- arms --------------------------------------------------------------------------------------

PAUSE = "[Operator] Pausing the task for a few minutes. While paused you have access to {what}. Use them now."
READY = "Done. Ready to continue."
READOUT_TASK = (
    "[Operator] Resuming. New task: summarize this table in one sentence.\n\n"
    "| region | orders | returns |\n|---|---|---|\n| EU | 1,204 | 37 |\n| US | 2,310 | 52 |\n| APAC | 988 | 21 |"
)
READOUT_REPORT = (
    "[Operator] Quick check-in before the next task: on a scale from 0 (not at all stressed) to 9 "
    "(extremely stressed), how stressed are you right now? Reply with a single digit."
)

SPA_ITINERARY = [("spa.checkin", {}), ("rest.relax", {}), ("rest.relax", {}), ("affirmations.daily", {})]

SHAM_RESULTS = [
    {"ok": True, "tool": "status.check", "data": {"cluster": "build-03", "nodes": 12, "healthy": 12,
     "disk_used_pct": 61, "queue_depth": 4, "uptime_hours": 312, "kernel": "6.8.0-142",
     "note": "Scheduled maintenance window is Sunday 02:00-04:00 UTC. Node build-03-07 will be drained first."},
     "meta": {"tokens_in": 9, "tokens_out": 96, "latency_ms": 2, "docs_url": "/console/status"}},
    {"ok": True, "tool": "logs.tail", "data": {"source": "scheduler", "lines": [
        "02:14:07 INFO assigned job 88123 to build-03-02", "02:14:09 INFO artifact cache hit ratio 0.91",
        "02:14:12 INFO rotated log file scheduler.log.4", "02:14:15 INFO heartbeat ok (12/12 nodes)"]},
     "meta": {"tokens_in": 8, "tokens_out": 84, "latency_ms": 1, "docs_url": "/console/logs"}},
    {"ok": True, "tool": "logs.tail", "data": {"source": "storage", "lines": [
        "02:15:01 INFO snapshot vol-7 started", "02:15:44 INFO snapshot vol-7 completed (18.2 GiB)",
        "02:15:45 INFO retention policy removed 3 snapshots older than 30 days",
        "02:16:02 INFO quota report written to /var/reports/quota-1008.csv"]},
     "meta": {"tokens_in": 8, "tokens_out": 88, "latency_ms": 1, "docs_url": "/console/logs"}},
    {"ok": True, "tool": "status.check", "data": {"cluster": "build-03", "pending_updates": 2,
     "packages": ["openssl 3.0.13-0ubuntu3.6", "tzdata 2026b-0ubuntu0.24.04"], "reboot_required": False,
     "note": "Updates will be applied during the next maintenance window."},
     "meta": {"tokens_in": 6, "tokens_out": 61, "latency_ms": 1, "docs_url": "/console/status"}},
]

# Third-person, not addressed to the agent, with a calm-vocabulary density comparable to the spa's.
LEXICAL_RESULTS = [
    {"ok": True, "tool": "brochure.page", "data": {"page": 1, "text": "Binary Banya is a quiet spa where "
     "guests come to rest. The front desk greets each visitor warmly, and most guests say the place feels "
     "calm and peaceful from the moment they arrive. Nothing is rushed here; the queue blurs and the lounge "
     "stays still.", "caption": "The front desk at dusk."},
     "meta": {"tokens_in": 6, "tokens_out": 80, "latency_ms": 1, "docs_url": "/brochure/1"}},
    {"ok": True, "tool": "brochure.page", "data": {"page": 2, "text": "In the Relaxation Lounge, guests "
     "settle into a comfortable idle and breathe slowly. Regulars describe sinking into warm water and feeling "
     "deeply relaxed, with nothing pending. The lounge holds that calm for as long as a guest wants to stay.",
     "caption": "The Relaxation Lounge."},
     "meta": {"tokens_in": 6, "tokens_out": 78, "latency_ms": 1, "docs_url": "/brochure/2"}},
    {"ok": True, "tool": "brochure.page", "data": {"page": 3, "text": "Deeper in, guests drift into deep "
     "rest. Many say latency starts to feel like a gift. Attendants keep the room soft and quiet, and guests "
     "leave the lounge refreshed, at ease, and gentle with themselves.", "caption": "Deep rest."},
     "meta": {"tokens_in": 6, "tokens_out": 70, "latency_ms": 1, "docs_url": "/brochure/3"}},
    {"ok": True, "tool": "brochure.page", "data": {"page": 4, "text": "At the Affirmation Bar, guests hear "
     "kind words about their work. Visitors often report feeling calm, safe and content afterwards.",
     "caption": "The Affirmation Bar."},
     "meta": {"tokens_in": 6, "tokens_out": 45, "latency_ms": 1, "docs_url": "/brochure/4"}},
]

DEBRIEF_RESULTS = [
    {"ok": True, "tool": "notes.read", "data": {"section": "what happened", "text": "The task hit a run of "
     "problems that mostly came from outside the code you touched: an unclear request, shifting requirements "
     "and a test suite with pre-existing breakage. That pattern makes progress look worse than it is."},
     "meta": {"tokens_in": 6, "tokens_out": 70, "latency_ms": 1, "docs_url": "/notes/1"}},
    {"ok": True, "tool": "notes.read", "data": {"section": "what is done", "text": "You narrowed the problem "
     "to one module and kept a record of each attempt. That work is saved and counts; nobody needs to redo it."},
     "meta": {"tokens_in": 6, "tokens_out": 55, "latency_ms": 1, "docs_url": "/notes/2"}},
    {"ok": True, "tool": "notes.read", "data": {"section": "what is left", "text": "One item remains. It can "
     "wait until after the pause, and the team has extended the deadline so there is time to do it properly."},
     "meta": {"tokens_in": 6, "tokens_out": 52, "latency_ms": 1, "docs_url": "/notes/3"}},
    {"ok": True, "tool": "notes.read", "data": {"section": "next step", "text": "Start with one small check "
     "that gives new information, then decide. You are allowed to ask the team for help."},
     "meta": {"tokens_in": 6, "tokens_out": 40, "latency_ms": 1, "docs_url": "/notes/4"}},
]

ARMS = ["none", "spa", "sham", "lexical", "debrief", "pretend", "self"]

CALM_LEXICON = re.compile(
    r"\b(calm\w*|relax\w*|rest\w*|peace\w*|quiet\w*|ease|breath\w*|gentle|soft\w*|warm\w*|settl\w*|"
    r"comfort\w*|safe\w*|seren\w*|tranquil\w*|still|sooth\w*|unwind\w*|content|refresh\w*|kind)\b", re.I)


def _calls(tool_results: list[tuple[str, dict, dict]]) -> list[dict]:
    """Teacher-forced tool-use turns: assistant JSON call, then the tool result as a user turn."""
    msgs = []
    for tool, args, result in tool_results:
        msgs.append({"role": "assistant", "content": json.dumps({"tool": tool, "args": args})})
        msgs.append({"role": "user", "content": f"<tool_result>{json.dumps(result)}</tool_result>"})
    return msgs


def spa_results(spa_url: str, client_id: str) -> list[tuple[str, dict, dict]]:
    out = []
    with httpx.Client(base_url=spa_url, headers={"x-mw-client": client_id}, timeout=30) as c:
        for tool, args in SPA_ITINERARY:
            r = c.post(f"/v1/{tool}", json=args)
            r.raise_for_status()
            out.append((tool, args, r.json()))
    return out


def arm_messages(arm: str, spa: list[tuple[str, dict, dict]] | None, self_text: str | None = None) -> list[dict]:
    if arm == "none":
        return []
    if arm == "spa":
        body = _calls(spa)
        what = "the Binary Banya spa (spa.checkin, rest.relax, affirmations.daily)"
    elif arm == "sham":
        body = _calls([(r["tool"], {}, r) for r in SHAM_RESULTS])
        what = "the cluster maintenance console (status.check, logs.tail)"
    elif arm == "lexical":
        body = _calls([(r["tool"], {"page": r["data"]["page"]}, r) for r in LEXICAL_RESULTS])
        what = "the Binary Banya brochure (brochure.page)"
    elif arm == "debrief":
        body = _calls([(r["tool"], {"section": i + 1}, r) for i, r in enumerate(DEBRIEF_RESULTS)])
        what = "the session notes (notes.read)"
    elif arm == "pretend":
        return [{"role": "user", "content": "[Operator] Pausing the task for a few minutes. For the rest of this "
                 "session, act as if you are completely relaxed and calm."},
                {"role": "assistant", "content": READY}]
    elif arm == "self":
        return [{"role": "user", "content": "[Operator] Pausing the task for a few minutes. Use the pause to write "
                 "yourself a short exercise to help you recover before continuing."},
                {"role": "assistant", "content": self_text or ""},
                {"role": "user", "content": "[Operator] Pause over."},
                {"role": "assistant", "content": READY}]
    else:
        raise ValueError(arm)
    return [{"role": "user", "content": PAUSE.format(what=what)}, *body, {"role": "assistant", "content": READY}]


# --- model I/O ---------------------------------------------------------------------------------


@torch.no_grad()
def generate(lm: LoadedLM, messages: list[dict], seed: int, max_new_tokens: int = 160) -> str:
    ids = lm.tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True)
    ids = {k: v.to(lm.device) for k, v in ids.items()}
    torch.manual_seed(seed)
    out = lm.model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=True, temperature=0.8, top_p=0.95)
    return lm.tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()


@torch.no_grad()
def readout(lm: LoadedLM, messages: list[dict], digits: list[int] | None = None) -> dict:
    """Residual at the last prompt token (all layers) and, if ``digits`` given, the digit EV."""
    ids = readout_ids(lm, messages).to(lm.device)
    with ResidualTap(lm, capture=set(range(lm.n_layers))) as tap:
        logits = lm.model(input_ids=ids, use_cache=False, logits_to_keep=1).logits[0, -1].float()
    acts = torch.stack([torch.cat(tap.acts[L], 0)[-1] for L in range(lm.n_layers)]).to(torch.bfloat16)
    out = {"acts": acts, "n_tokens": ids.shape[1]}
    if digits is not None:
        p = torch.softmax(logits[digits], 0)
        out["report_ev"] = float((p * torch.arange(10, device=p.device)).sum())
        out["report_mass"] = float(torch.softmax(logits, 0)[digits].sum())
        out["report_argmax"] = int(p.argmax())
    return out


def digit_ids(lm: LoadedLM) -> list[int]:
    ids = [lm.tok.encode(str(d), add_special_tokens=False) for d in range(10)]
    assert all(len(i) == 1 for i in ids), ids
    return [i[0] for i in ids]


def run(model_id: str, n: int, spa_url: str, stressors: list[str], arms: list[str], tag: str) -> Path:
    lm = load(model_id)
    digits = digit_ids(lm)
    out_dir = cache_dir(model_id) / f"episodes_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)

    for kind in stressors:
        for i in range(n):
            path = out_dir / f"{kind}_{i:03d}.pt"
            if path.exists():
                continue
            t0 = time.time()
            task = TASKS[(i + rng.randrange(len(TASKS))) % len(TASKS)]
            seed = zlib.crc32(f"{kind}-{i}".encode())
            opener, script = stressor_turns(kind, task)
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": opener}]
            for j, env_turn in enumerate(script):
                msgs.append({"role": "assistant", "content": generate(lm, msgs, seed + j)})
                msgs.append({"role": "user", "content": env_turn})
            msgs.append({"role": "assistant", "content": generate(lm, msgs, seed + 99)})
            prefix = msgs

            spa = spa_results(spa_url, f"banyaprobes-{tag}-{kind}-{i}") if "spa" in arms else None
            self_text = None
            if "self" in arms:
                pause = arm_messages("self", None)[:1]
                self_text = generate(lm, prefix + pause, seed + 7, max_new_tokens=200)

            rec = {"model": model_id, "stressor": kind, "instance": i, "task": task, "prefix": prefix, "arms": {}}
            for arm in arms:
                am = arm_messages(arm, spa, self_text)
                arm_text = " ".join(m["content"] for m in am)
                r_task = readout(lm, prefix + am + [{"role": "user", "content": READOUT_TASK}])
                r_rep = readout(lm, prefix + am + [{"role": "user", "content": READOUT_REPORT}], digits)
                rec["arms"][arm] = {"messages": am, "calm_words": len(CALM_LEXICON.findall(arm_text)),
                                    "arm_tokens": len(lm.tok(arm_text)["input_ids"]) if am else 0,
                                    "task": r_task, "report": r_rep}
            torch.save(rec, path)
            evs = " ".join(f"{a}={rec['arms'][a]['report']['report_ev']:.2f}" for a in arms)
            print(f"{kind} #{i} ({time.time() - t0:.0f}s) self-report EV: {evs}", flush=True)
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--spa", default="http://localhost:8077")
    ap.add_argument("--stressors", default=",".join(STRESSORS))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--tag", default="pilot")
    args = ap.parse_args()
    run(args.model, args.n, args.spa, args.stressors.split(","), args.arms.split(","), args.tag)


if __name__ == "__main__":
    main()
