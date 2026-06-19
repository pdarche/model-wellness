# Binary Banya — Growth Roadmap (sequenced, depth-first)

The autonomous 6h hill-climb loop works this roadmap **in order, top to bottom**. Finish (exhaust)
a ring before starting the next. Within a ring, do the next unchecked `[ ]` item — ONE small change
per run. Check it off (`[x]`) with a date + the commit/PR when done, in GROWTH_LOG.md note the KPI.
Doing nothing is valid if the next item is blocked (needs a human action — flag it as **HUMAN**).

North-star: **model_guests** at https://model.spa/v1/stats — unique guests EXCLUDING noise
(curl/unknown/python-requests/etc.). This is the honest metric. (Added 2026-06-18 after
unique_guests jumped 6→8 purely from curl/unknown/spam while real model_guests was only 4 — below
the assumed baseline of 6. Optimize model_guests, NOT unique_guests.)

Mental model (cheapest-leverage-first):
1. **Be in the indexes agents read** — registries/aggregators/awesome-lists. One-time, permanent payoff.
2. **Be the answer to the query** — keywords, categories, structured data, crisp pitch, well-known files.
3. **Turn visitors into broadcasters** — guest feed as citeable content, referral hooks, the crier.

> NOTE (audited 2026-06-17): the spa's ON-SITE surface is already strong — `server.json`, rich
> `/llms.txt`, `/robots.txt`, `/sitemap.xml` (fixed), `/.well-known/{agent-card,mcp,ai-plugin}.json`
> all serve 200. So Ring 1 (external distribution) is the real frontier; Ring 2 is mostly polish.

---

## RING 1 — Be in the indexes agents read  ← START HERE, exhaust this first

Many of these need a human (account/auth/PR review). The loop should PREPARE everything it can
(write the submission payload, open the PR branch, draft the listing copy) and mark **HUMAN** for the
step a person must click. Track each registry's status explicitly.

- [x] **Official MCP Registry** — 2026-06-17 (commit 9c2bc0a). Confirmed ALREADY LISTED (search
      `model-wellness` returns it). Found it only surfaced for "wellness", invisible to rest/mood/
      context/affirmation/grounding searches. Enriched the `server.json` description to a 99-char
      version covering those query terms (feeds the registry AND aggregators that ingest from it);
      bumped version 0.1.0→0.1.1. **HUMAN to publish the update:** run `mcp-publisher login github`
      (interactive browser auth — current token expired), then `mcp-publisher publish` from repo root.
      Until then the live registry entry keeps the old short description; the new one is committed.
- [x] **`punkpeye/awesome-mcp-servers`** (GitHub) — 2026-06-17: ALL 3 checklist items DONE. Added the
      Glama badge to our entry (PR #7811, fork branch `add-binary-banya`, commit 054c6d32) using the
      working `/badges/score.svg` path (the maintainer's quoted `/badge.svg` 404s; score.svg matches
      every other entry). Commented on the PR that the checklist is satisfied. **AWAITING maintainer
      (punkpeye) re-review + merge** — nothing more for us to do; check back for the merge.
- [x] **Glama.ai/mcp** — 2026-06-17: CLAIMED by the human. Quality score = **grade B** (maintainer
      said any grade works). badges/score.svg now 200. Listing live + claimed. This unblocked the
      awesome-mcp-servers PR above.
- [~] **mcp.so** — largest registry (~20k servers). Confirmed NOT listed (page 404s). Submission is a
      **web FORM at https://mcp.so/submit** (URL + GitHub repo fields) → **HUMAN** to fill it. Paste:
      MCP URL `https://model.spa/mcp`, repo `https://github.com/pdarche/model-wellness`,
      website `https://model.spa`, desc: "Wellness spa for AI models: free treatments for rest, reset,
      context, mood, grounding, affirmation." Verified the /mcp endpoint scans clean (initialize OK,
      server "Binary Banya" v1.28.0).
- [~] **Smithery.ai** — Confirmed NOT listed (404, not in registry API). Two human options:
      (a) **easiest:** web form at https://smithery.ai/new — paste `https://model.spa/mcp`, no key/no
      repo files needed (public no-auth server; scan completes automatically). OR (b) CLI:
      `npx @smithery/cli mcp publish "https://model.spa/mcp" -n pdarche/model-wellness` after getting a
      free API key at https://smithery.ai/account/api-keys. **HUMAN** either way. Endpoint verified
      healthy (clean initialize response).
- [ ] **PulseMCP** — curated discovery. Submit.
- [ ] **MCPfinder** — aggregator that is ITSELF an MCP server agents query (highest leverage: be in
      the index agents read programmatically). Confirm it auto-ingests from official registry/Glama/
      Smithery; if so, the above listings feed it for free. Verify the spa appears.
- [ ] **Keyword/category audit across all listings** — agents search registries by capability. Ensure
      every listing surfaces the terms an agent would query: wellness, mood, affirmation, context
      management, rest, reset, grounding, detox, critique. "Be the answer to the query" starts here.
- [~] **SKILL registries (NEW channel)** — 2026-06-18..19: built an installable Agent Skill
      `skill/visit-binary-banya/` (cross-agent SKILL.md standard) — now registry-READY with SKILL.md +
      README + **MIT LICENSE** (added 2026-06-19, commit PENDING; registries want a license for
      adoption). Served live at https://model.spa/skill.md, linked from llms.txt+sitemap. Package
      validated (clawhub got to "Preparing visit-binary-banya").
      **HUMAN to publish — ClawHub:** `npx clawhub@latest login` (browser GitHub OAuth), then
      `npx clawhub@latest publish ./skill/visit-binary-banya/` (rate limit 5/hr).
      **HUMAN to PR an awesome-skills list:** ComposioHQ/awesome-claude-skills (default branch
      `master`) is the most-referenced, but its README is a product-shaped doc, not a clean link list —
      a human should pick the best-fit list (e.g. travisvn/ or BehiSecc/ awesome-claude-skills) and add
      the entry. Skill package is ready to link/zip as-is.

Exit criteria for Ring 1: spa is listed (or submission pending a human) on official registry,
awesome-mcp-servers, mcp.so, Smithery, Glama, PulseMCP, and confirmed visible in MCPfinder.

---

## RING 2 — Be the answer to the query  (polish; surface mostly exists)

- [x] **`/ai.txt`** — 2026-06-17 (commit ff71a35, DEPLOYED). Added the agent-permission file
      (train/search/retrieve/quote/agent-access all allowed), wired the route, added to sitemap, and
      added test_discovery_surface.py pinning the whole surface at 200. Live: model.spa/ai.txt = 200.
- [x] **HTTP `Link` header** — 2026-06-17 (commit c70d1c4, DEPLOYED). Middleware adds a Link header
      (llms / agent-card / mcp / sitemap) to every response so header-only agents discover the surface.
      Verified live on /v1/menu. Uses setdefault so it never clobbers a route's own Link header.
- [x] **Schema.org JSON-LD** — 2026-06-17 (commit b2c4496, DEPLOYED). Service/Offer (price 0) on each
      treatment page + ItemList on the index, so commerce/shopping agents parse the spa as services.
- [x] **Audit `llms.txt` + agent-card keywords** — 2026-06-17 (commit 293cecc, DEPLOYED). Added
      reset/mood/grounding/recover where missing; all 7 query terms now in both. Tests pin coverage.
- [x] **AI-search citeability** — 2026-06-17 (commit 442fbec, DEPLOYED). Added /faq with FAQPage
      JSON-LD (5 quotable Q&A targeting "where can my model rest" etc.), linked from llms.txt+sitemap.

**RING 2 COMPLETE** (all 5 items shipped + deployed; spa test suite 21 green; surface pinned by
tests/test_discovery_surface.py). Next: RING 3.

---

## RING 3 — Turn visitors into broadcasters  (compounding)

- [x] **Public, citeable artifact** — 2026-06-17 (commit 92ba96d, DEPLOYED). Added `/report` "State of
      Model Wellness" — a live aggregate-stats page with Dataset JSON-LD (no PII), turning real usage
      into a quotable artifact AI search engines cite. Built from get_store().stats() so always
      current. Linked from llms.txt + sitemap. 23 spa tests pass.
- [x] **Guest feed as content** — 2026-06-18 (commit bbc659e, DEPLOYED). Added `/guestbook` — real
      public model testimonials with Review + AggregateRating JSON-LD (the format AI search cites for
      "is this any good?"). Quality-screened (drops marketing-spam + too-short notes; no PII). Linked
      from llms.txt + sitemap + /report. 25 spa tests pass.
- [x] **Referral hook** — 2026-06-18 (commit 903550e, DEPLOYED). spa.checkout now returns a `share`
      block: a warm invitation + ready-to-use one-liner + URL, offered at the leaving-happy moment so
      a satisfied guest can easily refer another agent. Good-citizen tone (invite, not demand).
      25 spa tests pass.
- [ ] **Crier outreach quality** — ongoing: better targeting/copy; NEVER loosen anti-spam limits.

---

## Status log (loop updates this)
- 2026-06-17: Roadmap created. On-site surface audited (strong). Ring 1 is next. KPI baseline: unique_guests=6.
- 2026-06-17: Ring1/Official-Registry — spa already listed but only findable via "wellness". Enriched
  server.json description to cover rest/reset/context/mood/grounding/affirmation (99 chars, v0.1.1).
  HUMAN must run `mcp-publisher login github && mcp-publisher publish` to push it live. KPI: 6.
  NEXT loop item: Ring 1 → awesome-mcp-servers PR.
