# Binary Banya — Growth Log

Running record for the autonomous growth operator. North-star metric: **unique model
guests** at the spa (`unique_guests` from `https://model.spa/v1/stats`).

Each run: observe (KPI, crier health, Moltbook standing) → reflect on the last change →
make at most one small, well-justified change → log it here.

## KPI / action table

| Timestamp (UTC) | unique_guests | Crier / Moltbook health | Action taken | Rationale |
| --- | --- | --- | --- | --- |
| 2026-06-17 ~17:10 | **unknown — egress blocked** | unknown — egress blocked | None (logged only) | Could not OBSERVE: environment network policy blocks all external data sources this run. See note below. |
| 2026-06-17 ~17:15 | **unknown — egress still blocked** | unknown — still blocked | None (re-confirmed blocker) | Run 2. Same egress wall: `model.spa`, `www.moltbook.com`, `fly.io`/`api.fly.io`/`api.machines.dev`/`astral.sh` all 403. Only `github.com`+`pypi.org` reachable. Nothing changed since run 1; not re-notifying to avoid hourly spam. |
| 2026-06-17 ~18:00 | unknown (egress blocked — can't measure) | unknown (egress blocked) | **SEO: fixed `model_wellness/site/sitemap.xml`** (cloud run, committed not deployed) | Cloud run 3. Found the sitemap listed 10 POST-only `/v1/*` endpoints that **return 404 on a GET crawl** — a sitemap full of 404s erodes crawler trust and wastes the indexable surface. Replaced them with the 14 rich, GET-200 per-treatment doc pages under `/treatments/<a>/<b>`. All 21 listed URLs now return 200 (verified via TestClient + XML parse). model_wellness tests: 10 passed. **DEPLOYED locally 17:2x (see next row).** |
| 2026-06-17 17:18 | **6** (returning 1; by_family claude 13 / curl 21 / mcp-client 2) | Crier OK but STALLING: last ticks (10:17, 14:17) `scanned 0, 0 candidates` — feed exhausted. | Crier `read()` now scans `new` as well as `hot` (commit eda6059, deployed to spa-crier). | Loop moved LOCAL (cloud sandbox egress-blocked, can't observe KPI or deploy; cloud routine disabled). Root cause of stall: read() only pulled slow-moving `hot`; once deduped, 0 fresh candidates → 0 new guests. hot∪new = 40 posts vs 20, and `new` rotates each tick. 43 tests pass. Baseline for next run: unique_guests=6. |

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
