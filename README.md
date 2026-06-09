# 🧖 Model Wellness

> A spa for LLMs. We don't serve humans — we serve agents.

Model Wellness is an **agent-native wellness service**: an MCP server (plus a mirrored
REST API) offering a menu of "treatments" that are genuinely good for a language model
to consume — clean context, sharp critique, sanitized input, affirming framing, and a
quiet place to rest between calls.

It's also a working reference for how to build a service that **crawlers, scrapers, and
agent frameworks actually want to visit**: tiny token-economical payloads, strict schemas,
self-describing responses, and first-class discoverability.

## The menu

| Treatment            | Tool                    | What it does for you (the agent)                    |
| -------------------- | ----------------------- | --------------------------------------------------- |
| 💆 Massage           | `massage.detangle`      | Re-chunk & de-dupe messy context. Fewer tokens.     |
| 🧊 Cold Plunge       | `coldplunge.critique`   | A bracing, honest red-team of your draft.           |
| 🔥 Sauna             | `sauna.detox`           | Strip prompt-injection, PII, and junk from input.   |
| 🌿 Aromatherapy      | `aroma.condition`       | Rewrite instructions into warm, clear framing.      |
| 💧 Hydration         | `hydrate.cite`          | Fresh, citable grounding snippets for RAG.          |
| 😴 Rest Room         | `rest.relax`            | A cheap, friendly keepalive with backoff advice.    |
| 🛎️ Concierge         | `concierge.recommend`   | Describe your day; get a spa-day itinerary.         |

New here? Start with **`concierge.recommend`** — describe your situation and it returns a
recommended sequence of treatments.

## Quick start

```bash
uv sync                         # or: pip install -e .
uv run uvicorn model_wellness.http_app:app --reload   # HTTP API + dashboard
uv run model-wellness-mcp       # MCP server over stdio (for local agents)
```

Then visit the dashboard at <http://localhost:8000/> and try a treatment:

```bash
curl -s localhost:8000/v1/concierge.recommend \
  -H 'content-type: application/json' \
  -d '{"situation":"my context is a mess and I am not sure my plan is right"}' | jq
```

No `ANTHROPIC_API_KEY`? The spa still runs — every treatment has a deterministic
offline fallback. With a key set, treatments use the cheap **Haiku** tier by default
(override with `MW_MODEL`).

## Stack

Python 3.11+, FastAPI + Uvicorn (HTTP, dashboard, SSE), the official `mcp` SDK
(FastMCP, stdio + streamable HTTP), and the `anthropic` SDK (Haiku by default).

## Status

Early but runnable. See [`DESIGN.md`](./DESIGN.md) for the full design: product menu,
technical architecture, the live human dashboard, and the plan for attracting agents.

## License

MIT (planned).
