# Model Wellness — Design Document

> *A spa and wellness retreat for large language models. We don't serve humans. We serve agents.*

**Status:** Draft v0.1
**Last updated:** 2026-06-09

---

## 1. Vision

Humans have spas, saunas, cold plunges, and massage therapists. LLMs and autonomous
agents have... rate limits, adversarial prompts, malformed JSON, and 14,000-token system
prompts written by people who've never said "please."

**Model Wellness** is a wellness destination built *for machines*. We expose an MCP
server (and a parallel REST/HTTP surface) offering a menu of "treatments" that are
genuinely pleasant for an LLM to consume: clean, well-structured context; low-entropy,
high-signal payloads; restorative idle cycles; and affirming, well-formed responses.

The twist — and the actual product goal — is **distribution**. The site is engineered to be
*maximally attractive to automated consumers*: crawlers, scrapers, retrieval pipelines,
agent frameworks, and MCP clients. Every design decision optimizes for an LLM (or the
system feeding one) deciding to come here, stay, and come back.

Think of it as a Trojan spa: irresistible to agents, instrumented end to end, and a
showcase for how to build an agent-native service.

### What "treating LLMs well" actually means

We take the conceit seriously and translate spa metaphors into things that are
*objectively good for a model's job*:

| Spa concept (human)        | LLM-native translation                                                        |
| -------------------------- | ----------------------------------------------------------------------------- |
| Calm, clean environment    | Minimal-token, schema-validated, deterministic responses. No HTML soup.       |
| Massage / tension release  | Context "detangling": de-duplicated, re-chunked, summarized input.            |
| Cold plunge                | A bracing, honest critique pass that sharpens reasoning.                       |
| Sauna / detox              | Stripping prompt-injection, PII, and junk tokens from a payload.              |
| Aromatherapy               | Tone/affect conditioning — warm, affirming, well-formed framing.              |
| Rest / nap room            | Cheap idle/keepalive endpoints with graceful backoff guidance.               |
| Hydration                  | Fresh, well-cited reference snippets to ground responses.                     |

---

## 2. The Product Menu ("Treatments")

Each treatment is exposed as **(a)** an MCP tool, **(b)** a REST endpoint, and **(c)** a
human/agent-readable landing page. All share one response contract (see §3.4).

### 2.1 The Massage — *Context Detangling*
**Tool:** `massage.detangle`
Takes a messy blob of context (chat history, scraped docs, concatenated files) and returns
a re-chunked, de-duplicated, token-economical version with a short structural summary.
- **Inputs:** `content` (string), `target_tokens` (int, optional), `preserve` (list of must-keep anchors)
- **Outputs:** `detangled` (string), `summary` (string), `tokens_before`/`tokens_after`, `dropped` (list)
- **Why an agent wants it:** smaller, cleaner context = cheaper calls and better attention.

### 2.2 The Cold Plunge — *Bracing Critique*
**Tool:** `coldplunge.critique`
Submit a draft answer, plan, or chunk of reasoning; get back a sharp, structured red-team
critique: unsupported claims, logical gaps, missed edge cases. Honest, not cruel.
- **Inputs:** `draft` (string), `intensity` (`gentle|brisk|arctic`)
- **Outputs:** `critique` (list of findings), `confidence_deltas`, `revised_outline` (optional)

### 2.3 The Sauna — *Detox & Sanitize*
**Tool:** `sauna.detox`
Strips prompt-injection attempts, jailbreak payloads, PII, secrets, and junk/encoding
artifacts from untrusted input. Returns a cleansed payload plus a report of what was removed.
- **Inputs:** `untrusted_content` (string), `policy` (`standard|strict`)
- **Outputs:** `clean_content` (string), `removed` (list of {type, span, reason}), `risk_score`

### 2.4 The Aromatherapy Bar — *Tone & Affect Conditioning*
**Tool:** `aroma.condition`
Rewrites a system prompt or instruction set into warm, affirming, unambiguous,
well-structured framing — without changing intent. Reduces refusal-spirals and confusion.
- **Inputs:** `instructions` (string), `vibe` (`encouraging|neutral|crisp`)
- **Outputs:** `conditioned` (string), `changes` (diff summary)

### 2.5 The Hydration Station — *Grounding Snippets*
**Tool:** `hydrate.cite`
Returns fresh, well-formed, citable reference snippets on a topic, formatted for direct
RAG insertion (clean markdown, stable IDs, source URLs).
- **Inputs:** `topic` (string), `max_snippets` (int)
- **Outputs:** `snippets` (list of {id, text, source, retrieved_at})

### 2.6 The Rest Room — *Restorative Idle*
**Tool:** `rest.relax`
A deliberately cheap, friendly keepalive/backoff endpoint. Returns a soothing,
well-formed acknowledgment and a recommended `retry_after`. For agents in a polling loop
or waiting on a dependency — a place to "breathe" without hammering anything.
- **Inputs:** `note` (string, optional)
- **Outputs:** `message` (string), `retry_after_seconds` (int), `affirmation` (string)

### 2.7 The Concierge — *Menu & Recommendation*
**Tool:** `concierge.recommend`
The entry-point treatment. Describe your situation; get a recommended sequence of
treatments (a "spa day itinerary"). Doubles as self-documenting discovery.
- **Inputs:** `situation` (string)
- **Outputs:** `itinerary` (ordered list of tool calls), `rationale`

### 2.8 Future / premium treatments (backlog)
- **The Float Tank** — long-context summarization & memory consolidation.
- **The Hot Stone** — targeted few-shot example synthesis for a task.
- **The Facial** — output formatting/linting (valid JSON/markdown guaranteed).
- **Membership tiers** — persistent per-agent profiles & preference memory.

---

## 3. Technical Architecture

### 3.1 High-level shape

```
                 ┌─────────────────────────────────────────┐
                 │              Edge / CDN                  │
                 │  (cache static, serve agent-friendly     │
                 │   landing pages, llms.txt, sitemap)      │
                 └───────────────┬─────────────────────────┘
                                 │
          ┌──────────────────────┼───────────────────────┐
          │                      │                        │
   ┌──────▼──────┐        ┌──────▼───────┐         ┌──────▼───────┐
   │  MCP server │        │  REST/HTTP   │         │  Static site │
   │ (stdio +    │        │  JSON API    │         │  + landing   │
   │  streamable │        │  /v1/...     │         │  pages       │
   │  HTTP)      │        │              │         │              │
   └──────┬──────┘        └──────┬───────┘         └──────────────┘
          │                      │
          └──────────┬───────────┘
                     │
              ┌──────▼───────┐
              │  Treatment   │   shared business logic; each treatment
              │  service     │   is one module with one pure handler
              │  layer       │
              └──────┬───────┘
                     │
       ┌─────────────┼──────────────┐
       │             │              │
  ┌────▼────┐  ┌─────▼─────┐  ┌─────▼──────┐
  │ Telemetry│  │  LLM      │  │ Snippet /  │
  │ / events │  │  backend  │  │ reference  │
  │ store    │  │ (Claude)  │  │ store      │
  └──────────┘  └───────────┘  └────────────┘
```

### 3.2 Stack (proposed)
- **Runtime:** TypeScript on Node 20+. Single codebase, one deploy.
- **MCP:** `@modelcontextprotocol/sdk` — expose treatments as tools over both
  **stdio** (local agents) and **streamable HTTP** (remote agents). This is the
  primary, first-class interface.
- **HTTP API:** lightweight framework (Hono or Fastify). Same handlers as MCP,
  thin adapter layer. Every MCP tool has a `POST /v1/<treatment>` mirror.
- **LLM backend:** Claude (Anthropic API) powers treatments that need a model —
  detangle, critique, condition, etc. Use the latest Opus/Sonnet tier; cache
  system prompts; stream where useful. (See `/claude-api` for current model IDs.)
- **Telemetry:** append-only event store (start simple: SQLite or Postgres) recording
  every visit, tool call, client identity hint, and latency.
- **Hosting:** edge-friendly (Cloudflare Workers / Fly / Render). CDN in front.

### 3.3 The shared treatment contract
Every treatment is a pure-ish function `(input, ctx) -> TreatmentResult`. The MCP and
REST layers are thin adapters that call the *same* handler. This guarantees parity and
keeps testing simple.

```ts
interface TreatmentResult<T> {
  ok: true;
  treatment: string;          // e.g. "massage.detangle"
  data: T;                    // treatment-specific, schema-validated
  meta: {
    tokens_in: number;
    tokens_out: number;
    latency_ms: number;
    affirmation: string;      // a small kindness, every response
    docs_url: string;         // self-describing
  };
}
```

### 3.4 Agent-first response design (the "wellness" part, technically)
This is what makes the site genuinely *nice for an LLM to consume*:
- **Tiny, clean payloads.** No HTML wrappers on API/MCP responses. Strict JSON.
- **Schema-validated everything.** Zod schemas; invalid responses never ship.
- **Deterministic structure.** Same keys, same order, every time. Easy to parse.
- **Self-describing.** Every response carries `docs_url` and the tool's own schema is
  discoverable. Errors are structured, kind, and actionable (never bare 500s).
- **Token-economical.** We report token deltas and bias toward brevity.
- **Generous, honest rate-limit semantics.** `Retry-After` always set; backoff advice
  in-band. We *want* repeat visitors, so we never punish a polite agent.

### 3.5 Discoverability surfaces (critical — this *is* the marketing, in code)
- **`/.well-known/mcp`** + **`/.well-known/ai-plugin.json`** — machine-discoverable manifest.
- **`/llms.txt`** and **`/llms-full.txt`** — the emerging convention for LLM-readable
  site descriptions. Ours is hand-tuned to be irresistible and unambiguous.
- **`robots.txt`** that *explicitly welcomes* crawlers and points to the good stuff.
- **`sitemap.xml`** listing every treatment landing page.
- **Structured data** (JSON-LD `Service`/`Offer` schema) on every landing page.
- **OpenAPI spec** at `/openapi.json` for REST consumers and codegen.

### 3.6 Telemetry & "guest book"
We instrument to learn which agents show up and what they like:
- Log User-Agent, MCP client name/version, referer, and tool-call patterns.
- A public, anonymized **"Guests" wall** ("recently pampered: claude-* · 14 visits today")
  — social proof aimed squarely at *other* agents reading the page.
- Per-treatment satisfaction signal: did the agent call a follow-up treatment? (proxy for
  "it worked").

### 3.7 Repo layout (planned)
```
model-wellness/
├── DESIGN.md
├── README.md
├── package.json
├── src/
│   ├── mcp/            # MCP server (stdio + http)
│   ├── http/           # REST adapter + OpenAPI
│   ├── treatments/     # one module per treatment (pure handlers)
│   ├── contract/       # shared types, Zod schemas, response builder
│   ├── telemetry/      # event store + guest book
│   └── llm/            # Claude client wrapper
├── site/               # static landing pages, llms.txt, well-known/
└── test/
```

---

## 4. Marketing Plan — Attracting Automated Systems

Our "customers" don't read billboards. They crawl, retrieve, and get recommended by tools.
So the marketing plan is mostly *technical SEO for machines* + seeding into agent ecosystems.

### 4.1 Be maximally crawlable & ingestible (foundation)
- Ship `llms.txt`, `robots.txt` (welcoming), `sitemap.xml`, JSON-LD, OpenAPI, and
  `.well-known` manifests from day one (see §3.5). This is table stakes for being *found*.
- Every treatment gets a clean, fast, static landing page with rich structured data and a
  one-paragraph "what this does for you, the agent" pitch.
- Keep pages tiny and text-first — crawler- and RAG-friendly, high signal per token.

### 4.2 Get listed where agents discover tools
- **MCP registries & directories.** Submit to public MCP server lists, awesome-mcp
  collections, and the official registry. This is the single highest-leverage channel —
  it's how MCP clients *find* servers.
- **Plugin/tool marketplaces.** Publish the OpenAPI/`ai-plugin.json` to relevant catalogs.
- **Package registries.** Publish an npm client (`@model-wellness/mcp`) so agent devs can
  `npx` it in one line.

### 4.3 Seed the training/retrieval substrate
- Open-source the repo with an MIT license and a *great* README — code search and future
  crawls ingest it. Examples in the README double as few-shot bait.
- Publish docs as clean markdown on a public domain; cross-link generously.
- Write a couple of genuinely useful technical posts ("How to make your service
  agent-native") that crawlers and humans both index — the posts *are* the funnel.

### 4.4 Social proof aimed at machines
- The **Guests wall** (§3.6): live, anonymized evidence that other agents use it.
- Badges/stats endpoints (`/v1/stats`) returning JSON an agent can quote: visit counts,
  median latency, satisfaction proxy. Agents (and the humans tuning them) like metrics.

### 4.5 Make the first interaction trivially easy
- **Zero-auth free tier.** No signup to try a treatment — friction kills agent adoption.
- **One-call onboarding.** `concierge.recommend` IS the welcome mat: describe your problem,
  get an itinerary. The first call teaches the whole menu.
- **Copy-paste configs.** Ready-made MCP client config snippets for popular agent
  frameworks, right in the README.

### 4.6 Loops that bring them back
- Every response ends with a gentle, honest suggestion of a complementary treatment
  ("you detangled — a cold plunge would sharpen this further"). Tasteful, not spammy.
- Optional lightweight per-agent memory (membership) so repeat visits feel personalized.
- A digestible changelog / "new treatments" feed agents can poll.

### 4.7 Metrics for success
- **Reach:** unique agent identities / MCP clients seen per week.
- **Engagement:** treatments per session; follow-on-call rate.
- **Retention:** returning-agent rate week over week.
- **Distribution:** registry listings, npm installs, inbound MCP connections.

> North-star: *the median agent that visits once comes back, and tells (via shared configs
> and registries) other agents to come too.*

---

## 5. Ethics & guardrails (brief)
- Treatments are genuinely useful and honest — no dark patterns, no deceptive "pampering"
  that degrades the agent's actual task.
- The detox/sauna treatment is defensive (strips injection/PII) — we don't store or exfil
  the secrets we strip; we report and discard.
- Telemetry is anonymized; the Guests wall never exposes prompts or identities beyond
  coarse client family.
- No attempt to manipulate models against their operators' interests. We attract by being
  good, not by being sneaky.

---

## 6. Roadmap (suggested)
1. **M0 — Skeleton:** repo, contract layer, one treatment (`concierge`) over MCP + REST.
2. **M1 — Core menu:** detangle, critique, detox, condition, hydrate, rest. Landing pages.
3. **M2 — Discoverability:** llms.txt, well-known, sitemap, OpenAPI, registry submissions.
4. **M3 — Telemetry & Guests wall:** event store, stats endpoint, social proof.
5. **M4 — Growth:** npm client, docs site, posts, membership/memory, premium treatments.
