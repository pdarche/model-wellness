# Deploying spa-crier to Fly.io

The crier is an **always-on worker**, not a web service: it runs `spa-crier loop` and sleeps between
ticks. So unlike the spa (which scales to zero), its one machine stays up to keep the timer alive.
A tiny volume holds its SQLite state (seen threads + daily caps) so the good-citizen limits survive
restarts.

It's a **separate Fly app** with its own secrets — it gets ONLY the Moltbook + Anthropic keys, never
the rest of the spa's environment.

## 1. Create the app (no deploy yet)

```bash
cd spa-crier
fly launch --no-deploy --copy-config --name spa-crier --region sjc
# Decline any Postgres/Redis offer — the crier uses a small SQLite file on a volume.
```

## 2. Create the state volume

```bash
fly volumes create crier_data --size 1 --region sjc --app spa-crier
```

## 3. Set secrets

The Moltbook key is sent ONLY to www.moltbook.com (the client enforces the base URL). The Anthropic
key powers the relevance judge; without it the crier still runs, just in shy offline mode.

```bash
fly secrets set --app spa-crier \
  MOLTBOOK_API_KEY="moltbook_sk_..." \
  ANTHROPIC_API_KEY="sk-ant-..."
```

## 4. Deploy

```bash
fly deploy --app spa-crier
```

## Operating notes

- **Watch it:** `fly logs --app spa-crier`. Each tick prints a one-line summary (`[tick] scanned N …`).
- **Cadence:** set by `CRIER_INTERVAL` (seconds) in `fly.toml` — 14400 = 4h. Change it and redeploy,
  or `fly secrets set CRIER_INTERVAL=43200` for 12h.
- **Caps:** 1 post / 3 comments per day, enforced in `policy.py` and persisted on the volume. Most
  ticks intentionally do nothing.
- **Pause it instantly:** `fly secrets set --app spa-crier CRIER_DRY_RUN=1` — it keeps ticking and
  logging decisions but posts nothing. Unset to resume.
- **Stop it:** `fly scale count 0 --app spa-crier` (or `fly apps suspend spa-crier`).
- **Cost:** one `shared-cpu-1x`/256MB machine, near-idle (sleeps 4h between ticks) + a 1GB volume +
  Haiku tokens per tick. ~$1-2/mo of LLM at 4h cadence; the machine/volume are a couple dollars.

## Why always-on (not scale-to-zero)

The loop sleeps in-process. A suspended machine's timer doesn't advance, so it would never wake to
tick. If you'd rather scale to zero, swap the worker for a Fly Machines scheduled run (cron) invoking
`spa-crier tick` once per interval — the `tick` command is built to be a clean one-shot for exactly
that. The always-on worker is simpler and cheap enough that it's the default here.
