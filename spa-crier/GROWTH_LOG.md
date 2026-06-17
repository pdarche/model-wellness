# Binary Banya — Growth Log

Running record for the autonomous growth operator. North-star metric: **unique model
guests** at the spa (`unique_guests` from `https://model.spa/v1/stats`).

Each run: observe (KPI, crier health, Moltbook standing) → reflect on the last change →
make at most one small, well-justified change → log it here.

## KPI / action table

| Timestamp (UTC) | unique_guests | Crier / Moltbook health | Action taken | Rationale |
| --- | --- | --- | --- | --- |
| 2026-06-17 ~17:10 | **unknown — egress blocked** | unknown — egress blocked | None (logged only) | Could not OBSERVE: environment network policy blocks all external data sources this run. See note below. |

## Notes

### 2026-06-17 — First run; blocked by network egress policy

This is the first logged run, so there are no prior changes to assess. Recent git history
(crier built across `b3be7db`…`bf997c9`: spa-relevant targeting, reply-to-own-posts,
spam-flag fix via write spacing + per-tick cap, hardened verification) predates this log;
their efficacy is unknown because I have no KPI baseline yet.

**Blocker:** the remote execution environment's network egress allowlist does not include
the hosts this routine depends on. Probed this run:

- `https://model.spa/...` → "Host not in allowlist" (cannot read the KPI `unique_guests`,
  guests, or any `/v1` endpoint).
- `https://www.moltbook.com/...` → "Host not in allowlist" (cannot check karma, unread,
  comment visibility, spam/verified status).
- `https://api.fly.io` / `https://fly.io` → HTTP 403 (cannot `flyctl logs` for crier
  health, and cannot deploy). `flyctl` is also not installed, and the installer at
  `fly.io/install.sh` is itself blocked, so it can't be bootstrapped.
- `https://pypi.org` → 200 (reachable). Git over the local proxy → reachable (this commit).

**Consequence:** the core OBSERVE step is impossible, so any code change would be made
blind and could not be test-deployed or verified. Per the guardrails (conservative;
doing nothing is valid; small changes only), I made **no change** and only recorded this.

**Action needed from the operator:** add these hosts to the environment's network egress
settings so future runs can do their job:
`model.spa`, `www.moltbook.com`, and `api.fly.io` (+ `fly.io` for the flyctl installer).
Docs: https://code.claude.com/docs/en/claude-code-on-the-web

**Baseline recorded for continuity** (from `spa_crier/config.py`, in case future runs want
to confirm the anti-spam caps are untouched): max 1 post/day, 3 comments/day, 6
replies/day, 2 replies/tick, min_relevance 0.55, feed_scan_size 25, dedupe on. Model
`claude-haiku-4-5-20251001`. **Do not loosen any of these.**
