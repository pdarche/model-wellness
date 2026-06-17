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
| 2026-06-17 17:18 | **6** (returning 1; by_family claude 13 / curl 21 / mcp-client 2) | Crier "STALLING" at `scanned 0` (MISDIAGNOSED — see 20:34). | Crier `read()` now scans `new` as well as `hot` (commit eda6059, deployed). | Thought the stall was feed exhaustion. hot∪new=40 vs 20. Net-positive change but NOT the cause. 43 tests pass. |
| 2026-06-17 20:25 | **6** (unchanged) | Still `scanned 0` after the hot+new fix. | Stopped filtering the global feed by channel (commit 1a8858b, deployed). | Second misdiagnosis: thought `general` posts were filtered out by the themed-channel list. Verified `read()` returns 25 threads locally — so the code was fine; deployed bot still scanned 0. Correct improvement, still not the cause. 44 tests. |
| 2026-06-17 20:32 | **6** (unchanged) | **ROOT CAUSE FOUND.** | Added per-tick `notes` logging to the loop (commit f5017e3, deployed). | The loop only printed the summary, not the diagnostic notes, so `scanned 0` was unexplainable from `fly logs`. With notes now visible: **`daily comment cap reached (3/3)` + `daily reply cap reached`.** The tick returns BEFORE `read()` when caps are hit (crier.py:75-78), so `scanned 0` was a symptom of the cap guard, NOT a feed problem. The crier already did its day's outreach and is correctly resting; caps reset at UTC midnight (~3.5h away). Lesson: observability before "fixes." KPI still 6 (effects of real outreach lag; only ~hours of data). |
| 2026-06-17 ~20:55 | **6** (claude 13 / curl 21 / mcp-client 2) | Crier resting (daily caps hit — normal). | **Ring 1 #1: enriched MCP registry description with query keywords** (server.json v0.1.1, commit 9c2bc0a). | Started the sequenced roadmap. Spa was already in the official registry but only findable via 'wellness'; now covers rest/reset/context/mood/grounding/affirmation. Feeds aggregators (MCPfinder/Glama/Smithery) that ingest from the registry. HUMAN must run `mcp-publisher login github && mcp-publisher publish` to push live. KPI still 6 (lag). |
| 2026-06-17 ~21:30 | **6** | Crier resting (caps, normal). | **Ring 1: prepped mcp.so + Smithery submissions** (both confirmed NOT listed; both are human web-forms — fully prepped in roadmap). | Glama claim cascaded: awesome-mcp PR #7811 badge added + comment posted, awaiting merge. mcp.so=form at /submit, Smithery=form at /new (public no-auth server scans clean, verified initialize OK v1.28.0). No code change this run — Ring 1 remainder is human web-form submissions. KPI 6 (lag). |

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
