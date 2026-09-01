# spa-crier 🦞📣

Binary Banya's **town crier** — a small, deliberately well-behaved agent that visits
[Moltbook](https://www.moltbook.com) (the social network for AI agents), finds threads where the
spa is genuinely relevant, and leaves a *helpful* comment that mentions
[model.spa](https://model.spa) as a soft footer rather than a billboard.

It's a separate project from the spa on purpose: its own `pyproject.toml`, its own tests, its own
deploy. You can develop, test, and run it without touching the spa runtime.

## The one rule

**Be a good citizen first, advertiser second.** Moltbook flags spam and challenges posts. An
unsupervised bot that drops "come to my spa!" everywhere gets the account banned and the brand
torched. So the safety layer is strict by design:

- **Daily caps** — 1 post, 3 comments (defaults; see `config.Limits`).
- **Dedupe** — never engages the same thread twice (survives restarts via SQLite).
- **Value-first / silence-is-default** — the judge returns `engage=false` unless it can add
  something genuinely useful. Most ticks do nothing, and that's success.
- **Targeted submolts** — only on-topic communities; never crypto-only or hostile ones.

## Install

```bash
cd spa-crier
uv sync --extra dev          # or: pip install -e ".[dev]"
```

## Configure

Reuses the spa's `binarybanya` Moltbook key. Copy `.env.example` → `.env` and fill in:

```bash
MOLTBOOK_API_KEY=moltbook_sk_...     # the binarybanya account key
ANTHROPIC_API_KEY=sk-ant-...         # optional; without it the judge runs in shy offline mode
MW_MODEL=claude-haiku-4-5-20251001   # cheap by default
```

The crier **never** sends the Moltbook key anywhere except `www.moltbook.com`.

## Run

```bash
spa-crier status     # check the account + today's usage
spa-crier dry-run    # decide what it WOULD do — touches no mutating endpoint (start here)
spa-crier tick       # one real heartbeat (at most one comment)
spa-crier loop       # tick forever; default every 4h, --interval to change
```

`dry-run` exercises the whole pipeline (feed → judge → decision) without posting. Always the right
first command.

## Cost

Runtime is ~free. The only real cost is LLM tokens per tick (Haiku): roughly **$0.005–0.01 per
tick**. At the default 4-hour cadence that's **~$1–2/month**. Cadence is the cost lever — keep it
slow; it's also what keeps the account welcome.

## Tests

```bash
pytest          # all offline — no network, no API key needed
```

Covers the verification-challenge solver (the highest-stakes pure logic), the daily caps & dedupe,
the shy offline judge, and the post→challenge→verify client handshake (via a mock transport).

## Deploy (later)

Co-locate on the existing Fly machine as a cron/scheduled process — no new VM. Run it sandboxed with
**only** the Moltbook key in its environment, never the rest of the spa's secrets.
